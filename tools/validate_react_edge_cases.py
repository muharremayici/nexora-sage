from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.artifact_validator import validate_against_schema
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, SCHEMAS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.source_files import count_source_lines
from tools.engines.a11y_i18n_contract_analyzer import analyze_a11y_i18n_file
from tools.engines.next_boundary_analyzer import analyze_next_boundary_file
from tools.engines.react_ecosystem_analyzer import analyze_react_ecosystem_file

MATRIX_PATH = CONFIG_DIR / "react_edge_case_matrix.json"
SCHEMA_PATH = SCHEMAS_DIR / "react_edge_case_matrix.schema.json"


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _fixture_contract(matrix: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    contract = matrix.get("fixture_contract", {}) if isinstance(matrix, dict) else {}
    fixture_cases = contract.get("fixture_cases", []) if isinstance(contract, dict) else []
    non_fixture_rows = contract.get("non_fixture_signals", []) if isinstance(contract, dict) else []
    cases = [row for row in fixture_cases if isinstance(row, dict)] if isinstance(fixture_cases, list) else []
    non_fixture = {
        str(row.get("signal"))
        for row in non_fixture_rows
        if isinstance(row, dict) and str(row.get("signal") or "").strip()
    }
    return cases, non_fixture


def _ecosystem_risks(project: str, rel_path: str, content: str) -> set[str]:
    row = analyze_react_ecosystem_file(project, rel_path, content, _atlas_fixture_for(rel_path, content)) or {}
    return {str(item.get("risk")) for item in row.get("findings", []) if isinstance(item, dict)}


def _atlas_fixture_for(rel_path: str, content: str) -> dict[str, Any]:
    features: list[str] = []
    line_count = max(1, count_source_lines(content))
    if "lazy(" in content or "dynamic(" in content:
        features.append("React:LazyBoundary")
    if "useQuery" in content:
        features.append("TanStack:Query")
    if "useMutation" in content:
        features.append("TanStack:Mutation")
    if "Provider" in content:
        features.append("React:ProviderTopology")
    if "'use client'" in content or '"use client"' in content:
        features.append("Next:ClientBoundary")
    return {
        "features": features,
        "symbols": [
            {
                "name": Path(rel_path).stem,
                "type": "Function",
                "features": features,
                "start": 1,
                "end": line_count,
                "source_lines": f"L1-L{line_count}",
            }
        ],
    }


def _next_risks(project: str, rel_path: str, content: str) -> set[str]:
    row = analyze_next_boundary_file(project, rel_path, content) or {}
    return {str(item) for item in row.get("risks", [])}


def _a11y_risks(project: str, rel_path: str, content: str) -> set[str]:
    row = analyze_a11y_i18n_file(project, rel_path, content) or {}
    return {str(item) for item in row.get("risks", [])}


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    matrix = load_json_file(MATRIX_PATH, {})
    schema_errors = validate_against_schema(SCHEMA_PATH, "react_edge_case_matrix", matrix)
    checks.append(_check("react_edge_case_matrix_schema_valid", not schema_errors, {"errors": schema_errors[:10]}))

    edge_cases = matrix.get("edge_cases", []) if isinstance(matrix, dict) else []
    fixture_cases, non_fixture_signals = _fixture_contract(matrix)
    ids = {str(item.get("id")) for item in edge_cases if isinstance(item, dict)}
    coverage_policy = matrix.get("coverage_policy", {}) if isinstance(matrix, dict) else {}
    required_ids = {
        str(item)
        for item in coverage_policy.get("required_edge_case_ids", [])
        if str(item).strip()
    } if isinstance(coverage_policy, dict) else set()
    checks.append(
        _check(
            "react_edge_case_matrix_covers_p0_p1_categories",
            required_ids.issubset(ids),
            {"missing": sorted(required_ids - ids), "total_edge_cases": len(edge_cases)},
        )
    )

    p0_statuses = set(coverage_policy.get("p0_required_statuses", [])) if isinstance(coverage_policy, dict) else set()
    p1_statuses = set(coverage_policy.get("p1_required_statuses", [])) if isinstance(coverage_policy, dict) else set()
    bad_statuses = [
        item.get("id")
        for item in edge_cases
        if isinstance(item, dict)
        and (
            (item.get("priority") == "P0" and item.get("status") not in p0_statuses)
            or (item.get("priority") == "P1" and item.get("status") not in p1_statuses)
        )
    ]
    checks.append(_check("react_edge_case_matrix_status_policy_holds", not bad_statuses, {"bad_statuses": bad_statuses}))

    fixture_signals = {
        str(signal)
        for fixture in fixture_cases
        for signal in fixture.get("expected", [])
        if str(signal).strip()
    }
    unresolved_fixture_signals = sorted(
        str(item.get("required_fixture_signal"))
        for item in edge_cases
        if isinstance(item, dict)
        and str(item.get("required_fixture_signal") or "").strip()
        and str(item.get("required_fixture_signal")) not in fixture_signals
        and str(item.get("required_fixture_signal")) not in non_fixture_signals
    )
    checks.append(
        _check(
            "react_edge_case_matrix_signals_have_fixture_or_declared_external_coverage",
            bool(fixture_cases) and not unresolved_fixture_signals,
            {"fixture_cases": len(fixture_cases), "unresolved_signals": unresolved_fixture_signals},
        )
    )

    fixture_results: list[dict[str, Any]] = []
    fixture_evidence_results: list[dict[str, Any]] = []
    for fixture in fixture_cases:
        rel_path = str(fixture.get("file") or "")
        content = str(fixture["content"])
        expected = set(fixture["expected"])
        actual = set()
        actual.update(_ecosystem_risks("MAIN", rel_path, content))
        ecosystem_row = analyze_react_ecosystem_file("MAIN", rel_path, content, _atlas_fixture_for(rel_path, content)) or {}
        ecosystem_findings = ecosystem_row.get("findings", []) if isinstance(ecosystem_row, dict) else []
        fixture_evidence_results.append(
            {
                "file": rel_path,
                "findings": len(ecosystem_findings),
                "atlas_feature_findings": sum(
                    1 for item in ecosystem_findings if "atlas_feature" in (item.get("evidence_kinds") or [])
                ),
                "ast_span_findings": sum(
                    1 for item in ecosystem_findings if "ast_span" in (item.get("evidence_kinds") or [])
                ),
            }
        )
        if fixture.get("next"):
            actual.update(_next_risks("MAIN", rel_path, content))
        if fixture.get("a11y"):
            actual.update(_a11y_risks("MAIN", rel_path, content))
        missing = sorted(expected - actual)
        unexpected = sorted(set(fixture.get("not_expected", [])) & actual)
        fixture_results.append(
            {
                "file": rel_path,
                "expected": sorted(expected),
                "not_expected": sorted(set(fixture.get("not_expected", []))),
                "actual": sorted(actual),
                "missing": missing,
                "unexpected": unexpected,
            }
        )

    checks.append(
        _check(
            "react_edge_case_fixture_detection_passes",
            all(not item["missing"] and not item["unexpected"] for item in fixture_results),
            {"fixtures": fixture_results},
        )
    )

    checks.append(
        _check(
            "react_edge_case_fixtures_are_atlas_evidence_backed",
            any(item["atlas_feature_findings"] for item in fixture_evidence_results)
            and any(item["ast_span_findings"] for item in fixture_evidence_results),
            {"fixtures": fixture_evidence_results},
        )
    )

    source_text = (CODE_MAPS_DIR / "tools" / "engines" / "react_ecosystem_analyzer.py").read_text(encoding="utf-8", errors="replace")
    checks.append(
        _check(
            "react_edge_case_detectors_are_source_backed",
            "FEATURE_FLAG_RE" in source_text
            and "HYDRATION_SENSITIVE_RE" in source_text
            and "GENERATED_CLIENT_RE" in source_text
            and "provider_topology_or_value_stability_risk" in source_text,
            "runtime branch, hydration, generated-client and provider topology detectors are present",
        )
    )

    payload = {
        "meta": {"kind": "react_edge_case_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
            "edge_cases": len(edge_cases),
            "fixture_cases": len(fixture_results),
        },
        "checks": checks,
        "fixture_results": fixture_results,
        "fixture_evidence_results": fixture_evidence_results,
    }
    save_json_atomic(RAW_DIR / "react_edge_case_validation.json", payload)
    lines = [
        "# React Edge Case Validation",
        "",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        f"- Edge cases: `{payload['summary']['edge_cases']}`",
        f"- Fixture cases: `{payload['summary']['fixture_cases']}`",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for check in checks:
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} |")
    save_text_atomic(REPORTS_DIR / "react_edge_case_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
