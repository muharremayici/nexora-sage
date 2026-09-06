from unittest.mock import patch

from tools.validate_react_universal_readiness import _artifact_status


def test_universal_readiness_rejects_empty_fixture_validation_shell():
    with patch(
        "tools.validate_react_universal_readiness.load_json_file",
        return_value={"summary": {"failed_checks": 0}},
    ):
        state = _artifact_status("react_fixture_matrix_validation")

    assert state["exists"] is True
    assert state["passed"] is False


def test_universal_readiness_accepts_observed_fixture_validation():
    payload = {
        "summary": {"failed_checks": 0, "total_checks": 1},
        "checks": [{"name": "fixture", "passed": True}],
    }
    with patch("tools.validate_react_universal_readiness.load_json_file", return_value=payload):
        state = _artifact_status("react_fixture_matrix_validation")

    assert state["passed"] is True
