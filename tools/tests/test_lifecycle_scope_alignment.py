from tools.validate_lifecycle import _project_scope_alignment_checks


def _results(checks):
    return {check.name: check for check in checks}


def test_scoped_runtime_artifacts_may_be_narrower_than_configured_topology():
    checks = _results(
        _project_scope_alignment_checks(
            {"MAIN", "VARIANT"},
            {"MAIN"},
            {"MAIN"},
            {"MAIN"},
        )
    )

    assert all(check.passed for check in checks.values())


def test_scoped_runtime_artifacts_may_be_narrower_than_preserved_canonical_atlas():
    checks = _results(
        _project_scope_alignment_checks(
            {"MAIN", "VARIANT"},
            {"MAIN", "VARIANT"},
            {"MAIN"},
            {"MAIN"},
        )
    )

    assert all(check.passed for check in checks.values())


def test_mixed_scope_downstream_artifacts_fail_closed():
    checks = _results(
        _project_scope_alignment_checks(
            {"MAIN", "VARIANT"},
            {"MAIN"},
            {"MAIN"},
            {"MAIN", "VARIANT"},
        )
    )

    assert checks["config_atlas_project_alignment"].passed is True
    assert checks["health_project_alignment"].passed is True
    assert checks["ai_context_project_alignment"].passed is False


def test_empty_atlas_scope_cannot_pass_alignment():
    checks = _project_scope_alignment_checks({"MAIN"}, set(), set(), set())

    assert all(check.passed is False for check in checks)
