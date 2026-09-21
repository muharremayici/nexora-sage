from __future__ import annotations

import copy

import pytest

from tools import run_distribution_tests as distribution_tests
from tools import validate_distribution_test_partition_receipts as partition_receipts


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


def test_operational_parity_contract_remains_private_maintainer_only() -> None:
    tests, _ = distribution_tests.resolve_tests("public_target_repository")
    assert "tools/tests/test_operational_parity_contract.py" not in tests

    if distribution_tests.PUBLIC_MANIFEST.is_file():
        return

    classified, _ = distribution_tests._canonical_classification(
        distribution_tests._load_profile()
    )
    assert (
        classified["tools/tests/test_operational_parity_contract.py"]
        == "private_maintainer"
    )


def test_unclassified_test_fails_closed() -> None:
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        pytest.skip("Canonical authority classification is not shipped publicly")
    payload = copy.deepcopy(distribution_tests._load_profile())
    public_rows = payload["classification"]["overrides"]["public_target_repository"]
    public_rows.remove("tools/tests/test_distribution_test_profiles.py")
    with pytest.raises(ValueError, match="explicit authority classification"):
        distribution_tests._canonical_classification(payload)


def test_partition_is_an_exact_ordered_union_for_both_profiles() -> None:
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        partitions, _, selected = distribution_tests.resolve_test_partitions(
            "public_target_repository"
        )
        tests, _ = distribution_tests.resolve_tests("public_target_repository")
        assert selected == "public_target_repository"
    else:
        partitions, _, selected = distribution_tests.resolve_test_partitions("canonical_all")
        tests, _ = distribution_tests.resolve_tests("canonical_all")
        assert selected == "canonical_all"

    flattened = [path for paths in partitions.values() for path in paths]
    assert sorted(flattened) == sorted(tests)
    assert len(flattened) == len(set(flattened))
    assert all(paths == sorted(paths) for paths in partitions.values())


def test_public_profile_has_an_explicit_nonempty_slice_in_every_family() -> None:
    partitions, _, selected = distribution_tests.resolve_test_partitions(
        "public_target_repository"
    )

    assert selected == "public_target_repository"
    assert all(partitions.values())


def test_unknown_or_multiply_owned_partition_token_fails_closed() -> None:
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        pytest.skip("Canonical partition ownership is not shipped publicly")
    payload = copy.deepcopy(distribution_tests._load_profile())
    classified, _ = distribution_tests._canonical_classification(payload)
    payload["partition"]["families"]["release_distribution"]["tokens"].remove(
        "distribution"
    )
    with pytest.raises(ValueError, match="token inventory mismatch"):
        distribution_tests._validate_partition_inventory(payload, sorted(classified))

    payload = copy.deepcopy(distribution_tests._load_profile())
    payload["partition"]["families"]["target_agent"]["tokens"].append("distribution")
    with pytest.raises(ValueError, match="multiple owners"):
        distribution_tests._partition_contract(payload)


def test_partition_membership_is_independent_of_input_order() -> None:
    payload = distribution_tests._load_profile()
    tests, _ = distribution_tests.resolve_tests("public_target_repository")
    forward = distribution_tests._partition_selected_tests(
        payload, tests, "public_target_repository"
    )
    reverse = distribution_tests._partition_selected_tests(
        payload, list(reversed(tests)), "public_target_repository"
    )

    assert forward == reverse


def _passing_shard_receipts(
    payload: dict, profile_name: str = "auto"
) -> dict[str, dict]:
    partitions, _, selected_profile = (
        distribution_tests._resolve_test_partitions_from_payload(payload, profile_name)
    )
    partition_sha256 = distribution_tests._canonical_sha256(payload["partition"])
    receipts: dict[str, dict] = {}
    for family, tests in partitions.items():
        paths_sha256 = distribution_tests._canonical_sha256(tests)
        receipts[family] = {
            "schema": partition_receipts.RECEIPT_SCHEMA,
            "status": "PASS",
            "authority": partition_receipts.RECEIPT_AUTHORITY,
            "profile": selected_profile,
            "collect_only": False,
            "partition_contract_sha256": partition_sha256,
            "selected_test_union_sha256": paths_sha256,
            "selected_test_count": len(tests),
            "exact_profile_union": False,
            "shards": [
                {
                    "id": family,
                    "status": "PASS",
                    "returncode": 0,
                    "test_count": len(tests),
                    "test_paths_sha256": paths_sha256,
                    "test_paths": tests,
                }
            ],
        }
    return receipts


def test_family_receipt_join_reconstructs_the_exact_profile_union() -> None:
    payload = distribution_tests._load_profile()
    receipts = _passing_shard_receipts(payload)

    combined = partition_receipts.combine_partition_receipts(
        payload,
        "auto",
        receipts,
    )
    tests, _ = distribution_tests.resolve_tests("auto")

    assert combined["status"] == "PASS"
    assert combined["exact_profile_union"] is True
    assert combined["selected_test_count"] == len(tests)
    assert [row["id"] for row in combined["shards"]] == list(
        payload["partition"]["execution_order"]
    )
    assert sorted(
        path for row in combined["shards"] for path in row["test_paths"]
    ) == tests


def test_family_receipt_join_rejects_missing_reordered_or_tampered_shards() -> None:
    payload = distribution_tests._load_profile()
    receipts = _passing_shard_receipts(payload)
    missing = dict(receipts)
    missing.pop("target_agent")
    with pytest.raises(ValueError, match="order/set mismatch"):
        partition_receipts.combine_partition_receipts(payload, "auto", missing)

    reordered = dict(reversed(list(receipts.items())))
    with pytest.raises(ValueError, match="order/set mismatch"):
        partition_receipts.combine_partition_receipts(payload, "auto", reordered)

    tampered = copy.deepcopy(receipts)
    tampered["governance_policy"]["shards"][0]["test_paths"].pop()
    with pytest.raises(ValueError, match="governance_policy"):
        partition_receipts.combine_partition_receipts(payload, "auto", tampered)


def test_family_receipt_join_rejects_non_pass_or_authority_drift() -> None:
    payload = distribution_tests._load_profile()
    failed = _passing_shard_receipts(payload)
    failed["runtime_platform"]["status"] = "FAIL"
    with pytest.raises(ValueError, match="runtime_platform"):
        partition_receipts.combine_partition_receipts(payload, "auto", failed)

    authority_drift = _passing_shard_receipts(payload)
    authority_drift["analysis_engines"]["authority"] = "release_authority"
    with pytest.raises(ValueError, match="analysis_engines"):
        partition_receipts.combine_partition_receipts(
            payload,
            "auto",
            authority_drift,
        )


def test_family_receipt_cli_inputs_are_confined_and_unique(
    tmp_path, monkeypatch
) -> None:
    raw_root = tmp_path / "output" / ".raw"
    raw_root.mkdir(parents=True)
    receipt = raw_root / "target.json"
    receipt.write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(partition_receipts, "ROOT", tmp_path)

    parsed = partition_receipts._parse_shard_receipts(
        [f"target_agent={receipt}"]
    )
    assert parsed == {"target_agent": receipt.resolve()}

    with pytest.raises(ValueError, match="escapes output/.raw"):
        partition_receipts._parse_shard_receipts(
            [f"target_agent={outside}"]
        )
    with pytest.raises(ValueError, match="duplicate"):
        partition_receipts._parse_shard_receipts(
            [
                f"target_agent={receipt}",
                f"target_agent={receipt}",
            ]
        )
