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
from tools.validate_react_fixtures import _bounded_fixture_config, bounded_react_fixture, react_proof_artifact_path


RAW_OUTPUT_PATH = RAW_DIR / "react_analysis_chain_integrity_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "react_analysis_chain_integrity_validation.md"
CONTRACT_PATH = CODE_MAPS_DIR / "config" / "react_analysis_chain_integrity_contract.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _load_contract() -> dict[str, Any]:
    payload = load_json_file(CONTRACT_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _react_primary_artifacts(contract: dict[str, Any], fixture_root: Path | None = None) -> dict[str, dict[str, Path]]:
    rows = contract.get("primary_artifacts") if isinstance(contract, dict) else []
    artifacts: dict[str, dict[str, Path]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        artifact_id = str(row.get("id") or "").strip()
        primary = str(row.get("primary") or "").strip()
        full = str(row.get("full") or "").strip()
        if artifact_id and primary and full:
            artifacts[artifact_id] = {
                "primary": react_proof_artifact_path(primary, RAW_DIR, fixture_root),
                "full": react_proof_artifact_path(full, RAW_DIR, fixture_root),
            }
    return artifacts


def _runtime_sensitive_dimensions(contract: dict[str, Any]) -> set[str]:
    values = contract.get("runtime_sensitive_dimensions") if isinstance(contract, dict) else []
    return {str(value) for value in values if isinstance(value, str) and value}


def _summary(payload: Any) -> dict[str, Any]:
    return payload.get("summary", {}) if isinstance(payload, dict) and isinstance(payload.get("summary"), dict) else {}


def _findings(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    findings = payload.get("findings", [])
    return [item for item in findings if isinstance(item, dict)] if isinstance(findings, list) else []


def _evidence_contract_check(artifacts: dict[str, dict[str, Path]]) -> dict[str, Any]:
    inspected: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    required_fields = {"evidence_ladder", "evidence_kinds", "runtime_proof_status", "evidence_spans"}

    for name, paths in artifacts.items():
        payload = load_json_file(paths["primary"], {})
        findings = _findings(payload)
        sample = findings[: min(50, len(findings))]
        for index, item in enumerate(sample):
            absent = sorted(field for field in required_fields if field not in item)
            if absent:
                missing.append({"artifact": name, "index": index, "missing": absent})
        inspected.append(
            {
                "artifact": name,
                "exists": paths["primary"].exists(),
                "findings": len(findings),
                "sampled": len(sample),
            }
        )

    return {"passed": not missing, "inspected": inspected, "missing": missing[:25]}


def _primary_full_contract_check(artifacts: dict[str, dict[str, Path]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for name, paths in artifacts.items():
        primary = load_json_file(paths["primary"], {})
        full = load_json_file(paths["full"], {})
        primary_summary = _summary(primary)
        full_summary = _summary(full)
        primary_findings = _findings(primary)
        full_findings = _findings(full)
        full_name = paths["full"].name
        declared_full = primary_summary.get("full_artifact")
        truncated = primary_summary.get("truncated_in_primary_report")
        if isinstance(truncated, bool):
            expected_truncated = len(full_findings) > len(primary_findings)
        else:
            expected_truncated = max(0, len(full_findings) - len(primary_findings))

        row = {
            "artifact": name,
            "primary_exists": paths["primary"].exists(),
            "full_exists": paths["full"].exists(),
            "primary_findings": len(primary_findings),
            "full_findings": len(full_findings),
            "declared_full_artifact": declared_full,
            "truncated_in_primary_report": truncated,
            "expected_truncated": expected_truncated,
            "full_summary_truncated": full_summary.get("truncated_in_primary_report"),
        }
        rows.append(row)

        if not paths["primary"].exists() or not paths["full"].exists():
            failures.append({"artifact": name, "reason": "missing primary or full artifact", "row": row})
        elif declared_full != full_name:
            failures.append({"artifact": name, "reason": "primary summary does not point to full artifact", "row": row})
        elif truncated != expected_truncated:
            failures.append({"artifact": name, "reason": "truncation summary does not match primary/full counts", "row": row})
        elif full_summary.get("truncated_in_primary_report") not in {False, 0, None}:
            failures.append({"artifact": name, "reason": "full artifact should not report itself as truncated", "row": row})

    return {"passed": not failures, "rows": rows, "failures": failures}


def _runtime_sensitive_proof_check(
    artifacts: dict[str, dict[str, Path]],
    runtime_sensitive_dimensions: set[str],
) -> dict[str, Any]:
    inspected = 0
    violations: list[dict[str, Any]] = []
    allowed_static_statuses = {"needs_runtime_proof", "static_correlated", "runtime_smoke_ready"}
    for name, paths in artifacts.items():
        payload = load_json_file(paths["full"], {})
        if not isinstance(payload, dict) or not payload.get("findings"):
            payload = load_json_file(paths["primary"], {})
        for item in _findings(payload):
            dimension = str(item.get("dimension") or "")
            evidence_kinds = {str(kind) for kind in item.get("evidence_kinds", []) if str(kind)}
            runtime_proof = str(item.get("runtime_proof_status") or "")
            confidence = str(item.get("confidence") or "")
            if dimension not in runtime_sensitive_dimensions:
                continue
            if evidence_kinds & {"runtime_smoke", "runtime_smoke_ready", "runtime_profile", "bundle_stats", "typescript_compiler"}:
                continue
            inspected += 1
            if runtime_proof not in allowed_static_statuses or confidence == "confirmed":
                violations.append(
                    {
                        "artifact": name,
                        "project": item.get("project"),
                        "file": item.get("file"),
                        "dimension": dimension,
                        "confidence": confidence,
                        "runtime_proof_status": runtime_proof,
                        "evidence_kinds": sorted(evidence_kinds),
                    }
                )
    return {"passed": not violations, "inspected": inspected, "violations": violations[:25]}


def _line_grounding_scope_check(artifacts: dict[str, dict[str, Path]]) -> dict[str, Any]:
    inspected = 0
    violations: list[dict[str, Any]] = []
    for name, paths in artifacts.items():
        payload = load_json_file(paths["primary"], {})
        findings = _findings(payload)
        for index, item in enumerate(findings):
            inspected += 1
            try:
                line = int(item.get("line") or 0)
            except (TypeError, ValueError):
                line = 0
            if line > 1:
                continue
            spans = item.get("evidence_spans") if isinstance(item.get("evidence_spans"), list) else []
            has_scope = bool(item.get("evidence_scope")) or any(
                isinstance(span, dict) and span.get("scope") for span in spans
            )
            if not has_scope:
                violations.append(
                    {
                        "artifact": name,
                        "index": index,
                        "project": item.get("project"),
                        "file": item.get("file"),
                        "dimension": item.get("dimension"),
                        "risk": item.get("risk"),
                        "line": item.get("line"),
                    }
                )
    return {"passed": not violations, "inspected": inspected, "violations": violations[:25]}


def _code_contract_check() -> dict[str, Any]:
    state_flow = _read(CODE_MAPS_DIR / "tools" / "engines" / "state_flow_scanner.py")
    react_ecosystem = _read(CODE_MAPS_DIR / "tools" / "engines" / "react_ecosystem_analyzer.py")
    react_runtime = _read(CODE_MAPS_DIR / "tools" / "engines" / "react_runtime_intelligence.py")
    react_frontier = _read(CODE_MAPS_DIR / "tools" / "engines" / "react_frontier_intelligence.py")
    edge_cases = _read(CODE_MAPS_DIR / "tools" / "validate_react_edge_cases.py")
    fixtures = _read(CODE_MAPS_DIR / "tools" / "validate_react_fixtures.py")

    checks = [
        _check(
            "state_flow_scanner_uses_current_pulse_atlas_contract",
            "from tools.core.atlas_io import resolve_atlas_data" in state_flow
            and "def run_state_flow_scanner(changed_files=None, atlas=None):" in state_flow
            and "atlas, atlas_input_source = resolve_atlas_data(atlas)" in state_flow,
            "state_flow_scanner.py",
        ),
        _check(
            "react_engines_attach_evidence_contract",
            all(
                "attach_react_evidence_contract" in text
                for text in (react_ecosystem, react_runtime, react_frontier)
            ),
            ["react_ecosystem_analyzer.py", "react_runtime_intelligence.py", "react_frontier_intelligence.py"],
        ),
        _check(
            "react_engines_use_atlas_evidence_kinds",
            all("atlas_evidence_kinds" in text for text in (react_ecosystem, react_runtime, react_frontier)),
            ["react_ecosystem_analyzer.py", "react_runtime_intelligence.py", "react_frontier_intelligence.py"],
        ),
        _check(
            "edge_case_validator_uses_atlas_fixture",
            "_atlas_fixture_for" in edge_cases and '"features": features' in edge_cases and '"source_lines"' in edge_cases,
            "validate_react_edge_cases.py",
        ),
        _check(
            "fixture_validator_samples_evidence_contract",
            "_react_evidence_contract_check" in fixtures and "evidence_ladder" in fixtures and "runtime_proof_status" in fixtures,
            "validate_react_fixtures.py",
        ),
    ]
    return {"passed": all(check["passed"] for check in checks), "checks": checks}


def run_validation() -> dict[str, Any]:
    with bounded_react_fixture() as fixture_root:
        return _run_validation(fixture_root)


def _run_validation(fixture_root: Path) -> dict[str, Any]:
    contract = _load_contract()
    artifacts = _react_primary_artifacts(contract, fixture_root)
    runtime_sensitive_dimensions = _runtime_sensitive_dimensions(contract)
    evidence = _evidence_contract_check(artifacts)
    primary_full = _primary_full_contract_check(artifacts)
    runtime_sensitive = _runtime_sensitive_proof_check(artifacts, runtime_sensitive_dimensions)
    line_grounding = _line_grounding_scope_check(artifacts)
    code_contracts = _code_contract_check()
    checks = [
        _check(
            "react_analysis_chain_scope_contract_loaded",
            CONTRACT_PATH.exists() and len(artifacts) >= 4 and len(runtime_sensitive_dimensions) >= 5,
            {
                "contract": str(CONTRACT_PATH.relative_to(CODE_MAPS_DIR)),
                "primary_artifact_count": len(artifacts),
                "runtime_sensitive_dimension_count": len(runtime_sensitive_dimensions),
            },
        ),
        _check("react_finding_evidence_contract", evidence["passed"], evidence),
        _check("react_primary_full_artifact_contract", primary_full["passed"], primary_full),
        _check("react_runtime_sensitive_findings_are_proof_gated", runtime_sensitive["passed"], runtime_sensitive),
        _check("react_findings_do_not_hide_default_line_collapse", line_grounding["passed"], line_grounding),
        _check("react_code_contracts_preserve_atlas_backed_analysis", code_contracts["passed"], code_contracts),
    ]
    summary = {
        "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "total_checks": len(checks),
        "passed_checks": sum(1 for check in checks if check.get("passed")),
        "failed_checks": sum(1 for check in checks if not check.get("passed")),
        "generated_at": _utc_now(),
    }
    payload = {
        "fixture_evidence": _bounded_fixture_config(),
        "meta": {
            "kind": "react_analysis_chain_integrity_validation",
            "version": "v1",
            "generator": "tools.validate_react_analysis_chain_integrity",
            "contract": str(CONTRACT_PATH.relative_to(CODE_MAPS_DIR)),
            "primary_artifacts": sorted(artifacts),
            "runtime_sensitive_dimensions": sorted(runtime_sensitive_dimensions),
        },
        "summary": summary,
        "checks": checks,
    }
    save_json_atomic(RAW_OUTPUT_PATH, payload)

    lines = [
        "# React Analysis Chain Integrity Validation",
        "",
        f"- Status: `{summary['status']}`",
        f"- Total checks: `{summary['total_checks']}`",
        f"- Passed: `{summary['passed_checks']}`",
        f"- Failed: `{summary['failed_checks']}`",
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
