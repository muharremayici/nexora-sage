from unittest.mock import patch

from tools.core.validator_progress import ValidatorProgress


def test_validator_progress_is_bounded_and_phase_aware(capsys):
    contract = {
        "observability": {
            "progress_every_items": 2,
            "progress_every_seconds": 60,
        }
    }
    with patch("tools.core.validator_progress.validator_execution_contract", return_value=contract):
        progress = ValidatorProgress("example_validator")
        progress.start()
        progress.phase("inventory", total=4)
        progress.advance(1, current_file="one.py")
        progress.advance(2, current_file="two.py")
        progress.complete("PASS", checks=1)

    output = capsys.readouterr().out
    assert "[example_validator] START" in output
    assert "PHASE name=inventory total=4" in output
    assert "PROGRESS phase=inventory completed=2 total=4" in output
    assert "current_file=one.py" not in output
    assert "[example_validator] PASS" in output
