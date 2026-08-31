from tools.engines.release_readiness_report import _artifact_passed as readiness_artifact_passed
from tools.validate_pipeline_layer_chain_integrity import _artifact_passed as chain_artifact_passed


def test_empty_pass_shell_is_not_release_evidence():
    payload = {"summary": {"status": "PASS", "failed_checks": 0}}

    assert readiness_artifact_passed(payload) is False
    assert chain_artifact_passed(payload)[0] is False


def test_nonempty_check_evidence_can_pass():
    payload = {
        "summary": {"status": "PASS", "failed_checks": 0, "total_checks": 1},
        "checks": [{"name": "observed", "passed": True}],
    }

    assert readiness_artifact_passed(payload) is True
    assert chain_artifact_passed(payload)[0] is True


def test_conflicting_pass_status_and_failed_check_fails_closed():
    payload = {
        "summary": {"status": "PASS", "failed_checks": 1, "total_checks": 1},
        "checks": [{"name": "failed", "passed": False}],
    }

    assert readiness_artifact_passed(payload) is False
    assert chain_artifact_passed(payload)[0] is False
