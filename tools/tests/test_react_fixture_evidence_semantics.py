from tools.validate_react_fixtures import _fixture_validation_passed


def test_empty_fixture_validation_shell_is_not_evidence():
    assert _fixture_validation_passed({"summary": {"failed_checks": 0}}) is False


def test_fixture_validation_requires_observed_checks():
    payload = {
        "summary": {"failed_checks": 0, "total_checks": 1},
        "checks": [{"name": "fixture_contract", "passed": True}],
    }

    assert _fixture_validation_passed(payload) is True


def test_fixture_validation_rejects_conflicting_pass_label():
    payload = {
        "summary": {"status": "PASS", "failed_checks": 1, "total_checks": 1},
        "checks": [{"name": "fixture_contract", "passed": False}],
    }

    assert _fixture_validation_passed(payload) is False
