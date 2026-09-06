from tools.validate_universal_proof import _external_evidence_complete


def test_duplicate_external_evidence_cannot_satisfy_threshold() -> None:
    assert not _external_evidence_complete(["a", "a"], ["a", "a"], 2)


def test_equal_counts_with_wrong_external_identity_fail() -> None:
    assert not _external_evidence_complete(["a", "b"], ["a", "c"], 2)


def test_exact_unique_external_identity_set_passes() -> None:
    assert _external_evidence_complete(["a", "b"], ["b", "a"], 2)
