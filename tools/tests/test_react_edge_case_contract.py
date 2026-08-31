import json
from pathlib import Path

from tools import validate_react_edge_cases


def test_react_edge_case_validator_rejects_unbound_matrix_signal(tmp_path, monkeypatch):
    matrix = json.loads(validate_react_edge_cases.MATRIX_PATH.read_text(encoding="utf-8"))
    matrix["edge_cases"][0]["required_fixture_signal"] = "missing_fixture_signal"
    matrix_path = tmp_path / "react_edge_case_matrix.json"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    monkeypatch.setattr(validate_react_edge_cases, "MATRIX_PATH", matrix_path)
    monkeypatch.setattr(validate_react_edge_cases, "RAW_DIR", Path(tmp_path / "raw"))
    monkeypatch.setattr(validate_react_edge_cases, "REPORTS_DIR", Path(tmp_path / "reports"))

    payload = validate_react_edge_cases.run_validation()
    signal_check = next(
        check
        for check in payload["checks"]
        if check["name"] == "react_edge_case_matrix_signals_have_fixture_or_declared_external_coverage"
    )

    assert payload["summary"]["failed_checks"] == 1
    assert signal_check["passed"] is False
    assert signal_check["details"]["unresolved_signals"] == ["missing_fixture_signal"]


def test_react_edge_case_validator_reads_required_ids_from_matrix_policy(tmp_path, monkeypatch):
    matrix = json.loads(validate_react_edge_cases.MATRIX_PATH.read_text(encoding="utf-8"))
    matrix["coverage_policy"]["required_edge_case_ids"].append("missing_required_edge_case")
    matrix_path = tmp_path / "react_edge_case_matrix.json"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    monkeypatch.setattr(validate_react_edge_cases, "MATRIX_PATH", matrix_path)
    monkeypatch.setattr(validate_react_edge_cases, "RAW_DIR", Path(tmp_path / "raw"))
    monkeypatch.setattr(validate_react_edge_cases, "REPORTS_DIR", Path(tmp_path / "reports"))

    payload = validate_react_edge_cases.run_validation()
    coverage_check = next(
        check for check in payload["checks"] if check["name"] == "react_edge_case_matrix_covers_p0_p1_categories"
    )

    assert payload["summary"]["failed_checks"] == 1
    assert coverage_check["passed"] is False
    assert coverage_check["details"]["missing"] == ["missing_required_edge_case"]
