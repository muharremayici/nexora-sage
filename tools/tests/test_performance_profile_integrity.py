from tools.validate_performance_profile_integrity import _dependency_gaps


def test_dependency_gaps_rejects_a_consumer_without_its_declared_producer() -> None:
    steps = [
        {
            "name": "Consumer",
            "depends_on": ["Required Producer"],
        }
    ]

    assert _dependency_gaps(steps) == [
        {
            "consumer": "Consumer",
            "missing_producers": ["Required Producer"],
        }
    ]


def test_dependency_gaps_accepts_dependency_closed_steps() -> None:
    steps = [
        {"name": "Required Producer", "depends_on": []},
        {"name": "Consumer", "depends_on": ["Required Producer"]},
    ]

    assert _dependency_gaps(steps) == []
