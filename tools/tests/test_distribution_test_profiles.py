from __future__ import annotations

import copy

import pytest

from tools import run_distribution_tests as distribution_tests


def test_active_distribution_test_inventory_is_exhaustive() -> None:
    payload = distribution_tests._load_profile()
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        tests, target_fixture = distribution_tests._projected_public_tests(payload)
        assert tests
        assert target_fixture
        return

    classified, test_glob = distribution_tests._canonical_classification(payload)
    discovered = {
        path.relative_to(distribution_tests.ROOT).as_posix()
        for path in distribution_tests.ROOT.glob(test_glob)
        if path.is_file()
    }
    assert set(classified) == discovered
    assert set(classified.values()) == {
        "private_maintainer",
        "public_target_repository",
    }


def test_public_profile_contains_no_private_maintainer_test() -> None:
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        tests, _ = distribution_tests.resolve_tests("public_target_repository")
        assert tests
        return

    classified, _ = distribution_tests._canonical_classification(
        distribution_tests._load_profile()
    )
    tests, _ = distribution_tests.resolve_tests("public_target_repository")
    assert tests
    assert all(classified[path] == "public_target_repository" for path in tests)


def test_unclassified_test_fails_closed() -> None:
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        pytest.skip("Canonical authority classification is not shipped publicly")
    payload = copy.deepcopy(distribution_tests._load_profile())
    public_rows = payload["classification"]["overrides"]["public_target_repository"]
    public_rows.remove("tools/tests/test_distribution_test_profiles.py")
    with pytest.raises(ValueError, match="explicit authority classification"):
        distribution_tests._canonical_classification(payload)
