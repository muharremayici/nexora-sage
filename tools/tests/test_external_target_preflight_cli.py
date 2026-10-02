"""Direct CLI boundaries for the standalone target preflight and public doctor."""

import sys
from pathlib import Path

import pytest

import codemaps
from tools import external_target_preflight
from tools.core import artifact_store


@pytest.mark.parametrize("form", ["positional", "option"])
def test_standalone_preflight_accepts_both_target_forms(monkeypatch, capsys, form):
    observed = []

    def fake_write(target_root, *, projects=None, trust_class=None):
        observed.append((target_root, projects, trust_class))
        return {"summary": {"status": "PASS"}}

    monkeypatch.setattr(external_target_preflight, "write_preflight", fake_write)
    args = ["C:/fixture/repository"] if form == "positional" else [
        "--target-root", "C:/fixture/repository"
    ]
    monkeypatch.setattr(sys, "argv", ["external_target_preflight.py", *args])

    assert external_target_preflight.main() == 0
    assert observed == [("C:/fixture/repository", None, "ordinary_unverified")]
    assert '"status": "PASS"' in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--target-root"],
        ["C:/fixture/first", "--target-root", "C:/fixture/second"],
        ["--unknown", "C:/fixture/repository"],
    ],
)
def test_standalone_preflight_rejects_invalid_arity_before_target_work(
    monkeypatch, capsys, args
):
    observed = []
    monkeypatch.setattr(
        external_target_preflight,
        "write_preflight",
        lambda *a, **k: observed.append((a, k)),
    )
    monkeypatch.setattr(sys, "argv", ["external_target_preflight.py", *args])

    with pytest.raises(SystemExit) as exc:
        external_target_preflight.main()
    assert exc.value.code == 2
    assert observed == []
    assert "error:" in capsys.readouterr().err


def test_standalone_preflight_help_does_not_start_target_work(monkeypatch, capsys):
    monkeypatch.setattr(
        external_target_preflight,
        "write_preflight",
        lambda *a, **k: pytest.fail("help must not scan a target"),
    )
    monkeypatch.setattr(sys, "argv", ["external_target_preflight.py", "--help"])

    with pytest.raises(SystemExit) as exc:
        external_target_preflight.main()
    assert exc.value.code == 0
    assert "--target-root" in capsys.readouterr().out


def test_doctor_target_root_redirects_before_storage_setup(monkeypatch, capsys):
    monkeypatch.setattr(
        artifact_store.STORE,
        "initialize_schema",
        lambda: pytest.fail("target-scoped doctor rejection must not initialize storage"),
    )
    monkeypatch.setattr(
        sys, "argv", ["sage.py", "doctor", "--target-root", "C:/fixture/repository"]
    )
    with pytest.raises(SystemExit) as exc:
        codemaps.main()
    assert exc.value.code == 2
    assert "external_target_preflight.py --target-root" in capsys.readouterr().err


def test_runbook_target_preflight_form_is_supported():
    runbook = (Path(__file__).resolve().parents[2] / "docs" / "FAILURE_MODE_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    assert "python tools/external_target_preflight.py --target-root <repo>" in runbook
    assert "doctor --target-root" in runbook
    assert "checks the SAGE installation, not the target repository" in runbook


def test_sibling_run_status_retains_target_scoped_argument():
    parser = codemaps.build_parser()
    args = parser.parse_args(
        ["run-status", "--run-id", "sage-run-fixture", "--target-root", "C:/fixture/repository"]
    )
    assert args.command == "run-status"
    assert args.target_root == "C:/fixture/repository"
