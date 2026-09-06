from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.engines import test_impact_matcher


RAW_OUTPUT_PATH = RAW_DIR / "test_impact_change_integrity_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "test_impact_change_integrity_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _dual_vector_fixture() -> dict[str, Any]:
    atlas = {
        "MAIN": {
            "files": {
                "src/Button.tsx": {},
                "src/__tests__/Button.test.tsx": {},
                "src/Card.tsx": {},
                "src/__tests__/Card.test.tsx": {},
            }
        }
    }
    nodes = {
        "MAIN::src/Button.tsx": {},
        "MAIN::src/__tests__/Button.test.tsx": {},
        "MAIN::src/Card.tsx": {},
        "MAIN::src/__tests__/Card.test.tsx": {},
    }
    rev_adj = {
        "MAIN::src/Button.tsx": ["MAIN::src/__tests__/Button.test.tsx"],
        "MAIN::src/Card.tsx": [],
        "MAIN::src/__tests__/Button.test.tsx": [],
        "MAIN::src/__tests__/Card.test.tsx": [],
    }

    original_loader = test_impact_matcher.load_atlas_data
    original_graph = test_impact_matcher._get_impact_graph
    try:
        test_impact_matcher.load_atlas_data = lambda: atlas
        test_impact_matcher._get_impact_graph = lambda _circular_deps=None: (nodes, rev_adj)
        button_result = test_impact_matcher.find_impacted_tests("MAIN::src/Button.tsx")
        card_result = test_impact_matcher.find_impacted_tests("MAIN::src/Card.tsx")
    finally:
        test_impact_matcher.load_atlas_data = original_loader
        test_impact_matcher._get_impact_graph = original_graph

    button_tests = button_result.get("impacted_tests", [])
    card_tests = card_result.get("impacted_tests", [])
    return {
        "button": button_tests,
        "card": card_tests,
        "dual_vector_promoted": bool(button_tests)
        and button_tests[0].get("type") == "Dual Vector Match"
        and float(button_tests[0].get("confidence", 0)) == 1.0,
        "semantic_match_detected": bool(card_tests)
        and card_tests[0].get("type") == "Semantic Convention Match"
        and float(card_tests[0].get("confidence", 0)) == 0.8,
    }


def run_validation() -> dict[str, Any]:
    matcher_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "test_impact_matcher.py")
    gap_text = _read(CODE_MAPS_DIR / "tools" / "generate_test_gap_report.py")
    profile_text = _read(CODE_MAPS_DIR / "config" / "test_impact_profiles.json")
    profile_validation = load_json_file(RAW_DIR / "test_impact_profile_validation.json", {})
    test_gap = load_json_file(RAW_DIR / "test_gap_report.json", {})
    fixture = _dual_vector_fixture()

    evidence_artifacts = set(test_gap.get("evidence_artifacts", []) if isinstance(test_gap, dict) else [])
    required_evidence = {
        "output/.raw/atlas.json",
        "output/.raw/circular_deps.json",
        "output/.raw/blast_radius.json",
        "output/.raw/signals.json",
    }
    summary = test_gap.get("summary", {}) if isinstance(test_gap, dict) else {}
    scope = test_gap.get("scope", {}) if isinstance(test_gap, dict) else {}
    default_scope = str(scope.get("default_agent_project_scope") or "MAIN")
    top_priorities = test_gap.get("top_surgical_priorities", []) if isinstance(test_gap, dict) else []
    changed_without_tests = (
        test_gap.get("changed_files_without_impacted_tests", []) if isinstance(test_gap, dict) else []
    )
    out_of_scope_priorities = [
        item
        for item in top_priorities
        if isinstance(item, dict)
        and default_scope not in {"*", "all", "ALL"}
        and str(item.get("project") or "") != default_scope
    ]
    p0_top_priorities = [
        item
        for item in top_priorities
        if isinstance(item, dict) and str(item.get("priority") or "") == "P0_ACTIVE_UNTESTED_CHANGE"
    ]

    checks = [
        _check(
            "test_impact_matcher_uses_atlas_and_dependency_graph",
            "load_atlas_data()" in matcher_text
            and "circular_deps.json" in matcher_text
            and "Static Dependency Tracing" in matcher_text
            and "Semantic Convention Match" in matcher_text,
            "tools/engines/test_impact_matcher.py",
        ),
        _check(
            "dual_vector_fixture_promotes_confidence",
            bool(fixture["dual_vector_promoted"]) and bool(fixture["semantic_match_detected"]),
            fixture,
        ),
        _check(
            "test_impact_profiles_are_config_driven_and_polyglot",
            "languages" in profile_text
            and "typescript" in profile_text
            and "python" in profile_text
            and "go" in profile_text
            and "java" in profile_text
            and "csharp" in profile_text
            and "tools.core.test_impact_profiles" in matcher_text,
            "config/test_impact_profiles.json",
        ),
        _check(
            "test_impact_profile_validator_passed",
            ((profile_validation.get("summary") or {}) if isinstance(profile_validation, dict) else {}).get("failed_checks", 1) == 0,
            profile_validation.get("summary", {}) if isinstance(profile_validation, dict) else {},
        ),
        _check(
            "test_gap_report_uses_contextos_blast_and_dependency_evidence",
            "_active_signal_nodes" in gap_text
            and "_blast_scores" in gap_text
            and "_load_reverse_dependency_graph" in gap_text
            and required_evidence.issubset(evidence_artifacts),
            {"evidence_artifacts": sorted(evidence_artifacts)},
        ),
        _check(
            "test_gap_artifact_is_agent_actionable",
            isinstance(summary, dict)
            and "top_surgical_priorities" in summary
            and isinstance(test_gap.get("top_surgical_priorities"), list)
            and isinstance(test_gap.get("changed_files_without_impacted_tests"), list)
            and isinstance(test_gap.get("agent_action_plan"), list),
            summary,
        ),
        _check(
            "test_gap_agent_lists_stay_default_project_scoped",
            isinstance(scope, dict)
            and default_scope
            and not out_of_scope_priorities
            and isinstance(test_gap.get("all_project_summary"), dict),
            {
                "default_agent_project_scope": default_scope,
                "out_of_scope_priorities": out_of_scope_priorities[:5],
                "all_project_summary": test_gap.get("all_project_summary", {}),
            },
        ),
        _check(
            "active_untested_changes_are_top_surgical_priorities",
            not changed_without_tests or bool(p0_top_priorities),
            {
                "changed_files_without_impacted_tests": len(changed_without_tests)
                if isinstance(changed_without_tests, list)
                else "not_list",
                "p0_top_surgical_priorities": len(p0_top_priorities),
            },
        ),
    ]

    validation_summary = {
        "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "total_checks": len(checks),
        "passed_checks": sum(1 for check in checks if check.get("passed")),
        "failed_checks": sum(1 for check in checks if not check.get("passed")),
        "generated_at": _utc_now(),
    }
    payload = {
        "meta": {
            "kind": "test_impact_change_integrity_validation",
            "version": "v1",
            "generator": "tools.validate_test_impact_change_integrity",
        },
        "summary": validation_summary,
        "checks": checks,
    }
    save_json_atomic(RAW_OUTPUT_PATH, payload)

    lines = [
        "# Test Impact / Change Integrity Validation",
        "",
        f"- Status: `{validation_summary['status']}`",
        f"- Total checks: `{validation_summary['total_checks']}`",
        f"- Passed: `{validation_summary['passed_checks']}`",
        f"- Failed: `{validation_summary['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        details = json.dumps(check.get("details"), ensure_ascii=False)
        details = details.replace("|", "\\|")
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {details} |")
    save_text_atomic(REPORT_OUTPUT_PATH, "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
