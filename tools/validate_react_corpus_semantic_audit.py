from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.evidence_status import evidence_passed
from tools.core.json_io import load_json_file


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _load_raw(name: str) -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / name, {})
    return payload if isinstance(payload, dict) else {}


def _load_corpus_config() -> dict[str, Any]:
    payload = load_json_file(CODE_MAPS_DIR / "config" / "react_corpus_saturation_matrix.json", {})
    return payload if isinstance(payload, dict) else {}


def _primary_public_corpus_doc(config: dict[str, Any]) -> Path:
    value = str(config.get("primary_public_corpus_doc") or "").strip()
    return CODE_MAPS_DIR / value


def _family_rows() -> list[dict[str, Any]]:
    report = _load_raw("react_corpus_saturation_report.json")
    return [row for row in report.get("families", []) if isinstance(row, dict)]


def _summary(name: str) -> dict[str, Any]:
    payload = _load_raw(name)
    return payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}


def validate_react_corpus_semantic_audit() -> dict[str, Any]:
    corpus_config = _load_corpus_config()
    corpus_doc_path = _primary_public_corpus_doc(corpus_config)
    saturation = _load_raw("react_corpus_saturation_report.json")
    saturation_summary = saturation.get("summary", {}) if isinstance(saturation.get("summary"), dict) else {}
    readiness_summary = _summary("react_universal_readiness.json")
    release_identity = load_json_file(CODE_MAPS_DIR / "config" / "release_identity.json", {})
    release_claim = release_identity.get("release_claim", {}) if isinstance(release_identity, dict) else {}
    identity_claim = str(release_claim.get("allowed") or "") if isinstance(release_claim, dict) else ""
    external_smoke = _load_raw("external_react_smoke_validation.json")
    external_summary = external_smoke.get("summary", {}) if isinstance(external_smoke.get("summary"), dict) else {}
    corpus_doc = _read(corpus_doc_path)
    corpus_doc_lower = corpus_doc.lower()
    rows = _family_rows()
    required_rows = [row for row in rows if row.get("required_for_release")]
    roadmap_rows = [row for row in rows if row.get("status") == "ROADMAP" or row.get("support_level") == "roadmap"]
    partial_rows = [row for row in rows if row.get("status") == "PARTIAL" or row.get("support_level") == "partial"]

    repo_only_rows = [
        row.get("id")
        for row in required_rows
        if (row.get("representative_repo_check") or {}).get("passed")
        and not (row.get("fixture_or_regression_check") or {}).get("passed")
    ]
    missing_doc_hits = [
        row.get("id")
        for row in required_rows
        if not row.get("source_doc_hits")
    ]
    broad_claim_phrases = [
        "statically proves every possible runtime react behavior",
        "full runtime/browser behavior proof",
        "production_ready_for_broad_react_ecosystem_including_native_shells",
    ]
    broad_claim_leaks = [phrase for phrase in broad_claim_phrases if phrase in corpus_doc_lower and "does not support" not in corpus_doc_lower]

    checks = [
        _check(
            "corpus_saturation_passes_required_behavior_families",
            saturation_summary.get("status") == "PASS"
            and saturation_summary.get("required_saturation_ratio") == 1.0
            and saturation_summary.get("passing_required_families") == saturation_summary.get("required_families"),
            saturation_summary,
        ),
        _check(
            "corpus_uses_behavior_family_not_repo_count_as_proof",
            saturation_summary.get("families", 0) >= 20
            and "behavior-family coverage instead of raw repository count" in _read(REPORTS_DIR / "react_corpus_saturation_report.md").lower(),
            {"families": saturation_summary.get("families")},
        ),
        _check(
            "required_families_have_doc_repo_and_fixture_regression_evidence",
            not repo_only_rows and not missing_doc_hits,
            {"repo_only_rows": repo_only_rows, "missing_doc_hits": missing_doc_hits},
        ),
        _check(
            "release_claim_is_static_evidence_backed_and_runtime_bounded",
            bool(identity_claim)
            and readiness_summary.get("allowed_claim") == identity_claim
            and readiness_summary.get("release_identity_claim") == identity_claim
            and readiness_summary.get("universal_ready") is True
            and "needs_runtime_proof" in str(readiness_summary.get("runtime_boundary", "")),
            {
                "roadmap_families": [row.get("id") for row in roadmap_rows],
                "partial_families": [row.get("id") for row in partial_rows],
                "readiness": readiness_summary,
            },
        ),
        _check(
            "external_react_smoke_is_quality_control_not_universality_basis",
            readiness_summary.get("external_fixture_role") == "real_world_calibration_evidence"
            and readiness_summary.get("external_fixture_pool_required") is False
            and evidence_passed(external_smoke, default=False),
            {"readiness": readiness_summary, "external_smoke": external_summary},
        ),
        _check(
            "public_corpus_doc_states_claim_boundary_and_runtime_limit",
            corpus_doc_path.exists()
            and "Claim Boundary" in corpus_doc
            and "Runtime tracing remains a v2 capability" in corpus_doc
            and "does not support unqualified claims" in corpus_doc,
            str(corpus_doc_path.relative_to(CODE_MAPS_DIR)),
        ),
        _check(
            "corpus_corrections_are_promoted_to_regression_proof",
            "Corrections Promoted From Corpus" in corpus_doc
            and "Regression Proof" in corpus_doc
            and "False-positive" not in corpus_doc[:2000],
            str(corpus_doc_path.relative_to(CODE_MAPS_DIR)),
        ),
        _check(
            "no_broad_runtime_claim_leaks_from_corpus_doc",
            not broad_claim_leaks,
            broad_claim_leaks,
        ),
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    payload = {
        "meta": {
            "kind": "react_corpus_semantic_audit",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_react_corpus_semantic_audit",
            "config": "config/react_corpus_saturation_matrix.json",
            "primary_public_corpus_doc": str(corpus_doc_path.relative_to(CODE_MAPS_DIR)),
        },
        "summary": {
            "status": status,
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
            "families": saturation_summary.get("families"),
            "required_families": saturation_summary.get("required_families"),
            "roadmap_families": len(roadmap_rows),
            "partial_families": len(partial_rows),
            "allowed_claim": readiness_summary.get("allowed_claim"),
        },
        "checks": checks,
        "sampled_artifacts": [
            "output/.raw/react_corpus_saturation_report.json",
            "output/.raw/react_universal_readiness.json",
            "output/.raw/external_react_smoke_validation.json",
            "config/release_identity.json",
            str(corpus_doc_path.relative_to(CODE_MAPS_DIR)),
        ],
    }
    save_json_atomic(RAW_DIR / "react_corpus_semantic_audit.json", payload)
    save_text_atomic(REPORTS_DIR / "react_corpus_semantic_audit.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# React Corpus Semantic Audit",
        "",
        "Checks whether external/corpus evidence is interpreted as scoped behavioral-family proof rather than broad runtime universality.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- families: `{summary.get('families')}`",
        f"- required_families: `{summary.get('required_families')}`",
        f"- allowed_claim: `{summary.get('allowed_claim')}`",
        "",
        "| Check | Passed |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = validate_react_corpus_semantic_audit()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
