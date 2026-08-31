from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file, load_json_object_strict


CLAIM_GUARD_POLICY_PATH = CONFIG_DIR / "claim_guard_policy.json"


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _claim_guard_policy() -> dict[str, Any]:
    return load_json_object_strict(CLAIM_GUARD_POLICY_PATH, label="Claim guard policy")


def _claim_docs(policy: dict[str, Any]) -> list[Path]:
    docs = policy.get("claim_docs", []) if isinstance(policy.get("claim_docs"), list) else []
    resolved: list[Path] = []
    for item in docs:
        rel = str(item or "").strip()
        if not rel:
            continue
        path = (ROOT / rel).resolve()
        try:
            path.relative_to(ROOT)
        except ValueError:
            continue
        resolved.append(path)
    return resolved


def _patterns(policy: dict[str, Any], group: str) -> list[re.Pattern[str]]:
    groups = policy.get("pattern_groups", {}) if isinstance(policy.get("pattern_groups"), dict) else {}
    raw_patterns = groups.get(group, []) if isinstance(groups.get(group), list) else []
    compiled: list[re.Pattern[str]] = []
    for raw in raw_patterns:
        try:
            compiled.append(re.compile(str(raw), re.IGNORECASE))
        except re.error:
            continue
    return compiled


def _pattern_compile_errors(policy: dict[str, Any]) -> list[dict[str, Any]]:
    groups = policy.get("pattern_groups", {}) if isinstance(policy.get("pattern_groups"), dict) else {}
    errors: list[dict[str, Any]] = []
    for group, raw_patterns in groups.items():
        if not isinstance(raw_patterns, list):
            errors.append({"group": str(group), "pattern": None, "error": "pattern group must be a list"})
            continue
        for raw in raw_patterns:
            try:
                re.compile(str(raw), re.IGNORECASE)
            except re.error as exc:
                errors.append({"group": str(group), "pattern": str(raw), "error": str(exc)})
    return errors


def _negated_claim_hints(policy: dict[str, Any]) -> tuple[str, ...]:
    hints = policy.get("negated_claim_hints", []) if isinstance(policy.get("negated_claim_hints"), list) else []
    return tuple(str(item).lower() for item in hints if str(item).strip())


def _is_negated_claim_line(line: str, policy: dict[str, Any]) -> bool:
    lowered = line.lower()
    return any(hint in lowered for hint in _negated_claim_hints(policy))


def _scan_docs(policy: dict[str, Any], patterns: list[re.Pattern[str]], *, ignore_negated: bool = False) -> list[dict[str, Any]]:
    hits = []
    for path in _claim_docs(policy):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if ignore_negated and _is_negated_claim_line(line, policy):
                continue
            for pattern in patterns:
                if pattern.search(line):
                    hits.append({"file": str(path.relative_to(ROOT)), "line": lineno, "text": line.strip()[:240]})
    return hits


def _policy_valid(policy: dict[str, Any]) -> bool:
    return (
        policy.get("_meta", {}).get("kind") == "claim_guard_policy"
        and bool(_claim_docs(policy))
        and not _pattern_compile_errors(policy)
        and bool(_patterns(policy, "broad_react"))
        and bool(_patterns(policy, "absolute_ready"))
        and bool(_patterns(policy, "broad_security"))
        and bool(_negated_claim_hints(policy))
    )


def run_validation() -> dict[str, Any]:
    release = load_json_file(RAW_DIR / "release_readiness.json", {})
    react = load_json_file(RAW_DIR / "react_universal_readiness.json", {})
    oracle = load_json_file(RAW_DIR / "architecture_oracle.json", {})
    taxonomy = load_json_object_strict(CONFIG_DIR / "react_fixture_family_taxonomy.json", label="React fixture family taxonomy")
    release_identity = load_json_object_strict(CONFIG_DIR / "release_identity.json", label="Release identity")
    claim_policy = _claim_guard_policy()
    external_oracle_doc = ROOT / "docs" / "ARCHITECTURE_ORACLE_EXTERNAL_BLUEPRINT_VALIDATION_2026-05-22.md"

    release_summary = release.get("summary", {}) if isinstance(release, dict) else {}
    react_summary = react.get("summary", {}) if isinstance(react, dict) else {}
    oracle_summary = oracle.get("summary", {}) if isinstance(oracle, dict) else {}
    policy = taxonomy.get("release_policy", {}) if isinstance(taxonomy, dict) else {}
    identity_release_claim = (
        (release_identity.get("release_claim") or {}) if isinstance(release_identity, dict) else {}
    )
    identity_claim = str(identity_release_claim.get("allowed") or "")
    allowed_claim = str(react_summary.get("allowed_claim") or "")
    universal_ready = bool(react_summary.get("universal_ready"))
    current_claim = str(policy.get("allowed_claim") or identity_claim)
    allow_broad_claim = universal_ready and allowed_claim == current_claim
    expected_claim = current_claim
    broad_react_hits = _scan_docs(claim_policy, _patterns(claim_policy, "broad_react"), ignore_negated=True)
    absolute_ready_hits = _scan_docs(claim_policy, _patterns(claim_policy, "absolute_ready"))
    broad_security_hits = _scan_docs(claim_policy, _patterns(claim_policy, "broad_security"), ignore_negated=True)
    pattern_compile_errors = _pattern_compile_errors(claim_policy)

    checks = [
        _check(
            "claim_guard_policy_is_valid",
            _policy_valid(claim_policy),
            {
                "policy": str(CLAIM_GUARD_POLICY_PATH.relative_to(ROOT)),
                "claim_docs": len(_claim_docs(claim_policy)),
                "pattern_groups": sorted((claim_policy.get("pattern_groups") or {}).keys()) if isinstance(claim_policy.get("pattern_groups"), dict) else [],
                "pattern_compile_errors": pattern_compile_errors,
            },
        ),
        _check(
            "release_readiness_snapshot_is_current_claim_source",
            release.get("readiness") == "PRODUCTION_READY"
            and release_summary.get("react_allowed_claim") == allowed_claim,
            {"readiness": release.get("readiness"), "release_summary": release_summary, "react_allowed_claim": allowed_claim},
        ),
        _check(
            "react_claim_requires_universal_readiness",
            universal_ready and allowed_claim == current_claim,
            {"universal_ready": universal_ready, "allowed_claim": allowed_claim},
        ),
        _check(
            "react_claim_respects_current_release_policy",
            allowed_claim == expected_claim and bool(identity_claim) and current_claim == identity_claim,
            {
                "allowed_claim": allowed_claim,
                "expected_claim": expected_claim,
                "current_claim_policy": current_claim,
                "release_identity_claim": identity_claim,
                "broad_claim_allowed_by_policy": allow_broad_claim,
            },
        ),
        _check(
            "docs_do_not_claim_full_react_universality_without_universal_ready",
            universal_ready or not broad_react_hits,
            broad_react_hits,
        ),
        _check(
            "production_ready_docs_are_backed_by_release_readiness",
            release.get("readiness") == "PRODUCTION_READY" or not absolute_ready_hits,
            absolute_ready_hits,
        ),
        _check(
            "docs_do_not_claim_full_sast_or_appsec_replacement",
            not broad_security_hits,
            broad_security_hits,
        ),
        _check(
            "architecture_oracle_external_blueprint_evidence_present",
            external_oracle_doc.exists()
            and oracle_summary.get("hard_gate_enforced") is False
            and oracle_summary.get("project_count", 0) >= 1,
            {"doc": str(external_oracle_doc.relative_to(ROOT)), "oracle_summary": oracle_summary},
        ),
    ]

    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {"kind": "claim_guard_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
            "allowed_claim": allowed_claim,
            "current_claim_policy": current_claim,
            "release_readiness": release.get("readiness"),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "claim_guard_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "claim_guard_validation.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Claim Guard Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- release_readiness: `{summary.get('release_readiness')}`",
        f"- allowed_claim: `{summary.get('allowed_claim')}`",
        f"- current_claim_policy: `{summary.get('current_claim_policy')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []):
        details = json.dumps(check.get("details"), ensure_ascii=False)[:500]
        lines.append(f"| `{check.get('name')}` | `{'PASS' if check.get('passed') else 'FAIL'}` | `{details}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
