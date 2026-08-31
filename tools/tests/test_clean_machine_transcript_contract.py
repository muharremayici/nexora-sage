import shutil
import subprocess
import sys

import pytest

from tools.generate_clean_machine_transcript import build_template
from tools.generate_phase_status import _clean_machine_installation_verdict


def test_clean_machine_template_preserves_baseline_stream_and_exit_evidence() -> None:
    rendered = build_template(
        "operator",
        "PENDING",
        "",
        r"C:\target repo",
        "fresh_prerequisites",
    )

    assert "Machine baseline: `fresh_prerequisites`" in rendered
    assert "DOCTOR_BEFORE_EXIT" in rendered
    assert "INSTALL_PROOF_EXIT" in rendered
    assert "INSTALL_PROOF_STRUCTURED_PASS" in rendered
    assert "DOCTOR_AFTER_EXIT" in rendered
    assert "1> $stdout 2> $stderr" in rendered
    assert "redirected or merged native stderr diagnostics" in rendered
    assert "$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)" in rendered
    assert "[Console]::InputEncoding = $Utf8NoBom" in rendered
    assert "[Console]::OutputEncoding = $Utf8NoBom" in rendered
    assert "$OutputEncoding = $Utf8NoBom" in rendered
    assert '$env:PYTHONUTF8 = "1"' in rendered
    assert '$env:PYTHONIOENCODING = "utf-8"' in rendered
    assert "$previousErrorActionPreference = $ErrorActionPreference" in rendered
    assert '$ErrorActionPreference = "Continue"' in rendered
    assert "$ErrorActionPreference = $previousErrorActionPreference" in rendered
    assert "$exit = 127" in rendered
    assert "$LASTEXITCODE = 127" not in rendered
    assert "release-check" not in rendered
    assert "& python -c" not in rendered
    assert "CLEAN_MACHINE_INSTALLATION_VERDICT: PENDING" in rendered
    assert "TARGET_REPOSITORY_GOVERNANCE_VERDICT: NOT_EVALUATED" in rendered
    assert "CLEAN_MACHINE_VERDICT: PENDING" in rendered
    assert "governance is PASS" not in rendered


def test_clean_machine_template_quotes_powershell_literal_target() -> None:
    rendered = build_template(
        "operator",
        "PENDING",
        "",
        r"C:\team's repo",
        "preprovisioned",
    )

    assert r"Resolve-Path -LiteralPath 'C:\team''s repo'" in rendered


def test_clean_machine_template_preserves_unicode_target_identity() -> None:
    target = "C:\\Sage-Clean-Test-T\u00fcrk\u00e7e\\repository"
    rendered = build_template("operator", "PENDING", "", target, "fresh_prerequisites")

    assert target in rendered


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell 5 is unavailable")
def test_windows_powershell_native_capture_preserves_utf8_codepoints(tmp_path) -> None:
    rendered = build_template("operator", "PENDING", "", r"C:\target", "fresh_prerequisites")
    setup_start = rendered.index("$Utf8NoBom =")
    setup_end = rendered.index("function Invoke-SageEvidence")
    utf8_setup = rendered[setup_start:setup_end].strip()

    producer = tmp_path / "unicode_producer.py"
    stdout = tmp_path / "stdout.txt"
    stderr = tmp_path / "stderr.txt"
    receipt = tmp_path / "receipt.txt"
    producer.write_text("print('T\\u00fcrk\\u00e7e')\n", encoding="utf-8")

    def ps_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = "\n".join(
        [
            utf8_setup,
            f"& {ps_literal(sys.executable)} -u {ps_literal(str(producer))} 1> {ps_literal(str(stdout))} 2> {ps_literal(str(stderr))}",
            "$NativeExit = $LASTEXITCODE",
            f"$Value = (Get-Content -LiteralPath {ps_literal(str(stdout))} -Raw).Trim()",
            "$Codepoints = (($Value.ToCharArray() | ForEach-Object { [int][char]$_ }) -join ',')",
            f"Set-Content -LiteralPath {ps_literal(str(receipt))} -Value $Codepoints -Encoding ASCII",
            "exit $NativeExit",
        ]
    )
    probe = tmp_path / "utf8_capture_probe.ps1"
    probe.write_text(script, encoding="utf-8-sig")

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert receipt.read_text(encoding="ascii").strip() == "84,252,114,107,231,101"


@pytest.mark.parametrize(
    ("caller_policy", "native_exit"),
    [("Stop", 0), ("Stop", 7), ("SilentlyContinue", 0)],
)
@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell 5 is unavailable")
def test_windows_powershell_wrapper_preserves_stderr_exit_and_caller_policy(
    tmp_path, caller_policy: str, native_exit: int
) -> None:
    rendered = build_template("operator", "PENDING", "", r"C:\target", "fresh_prerequisites")
    setup_start = rendered.index("$Utf8NoBom =")
    function_start = rendered.index("function Invoke-SageEvidence")
    function_end = rendered.index("```", function_start)
    utf8_setup = rendered[setup_start:function_start].strip()
    wrapper = rendered[function_start:function_end].strip()

    producer = tmp_path / "sage.py"
    evidence_root = tmp_path / "evidence"
    receipt = tmp_path / "receipt.txt"
    producer.write_text(
        "import sys\n"
        "print('T\\u00fcrk\\u00e7e')\n"
        "print('expected self-heal warning', file=sys.stderr)\n"
        "raise SystemExit(int(sys.argv[1]))\n",
        encoding="utf-8",
    )

    def ps_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = "\n".join(
        [
            f'$ErrorActionPreference = "{caller_policy}"',
            f"$EvidenceRoot = {ps_literal(str(evidence_root))}",
            "New-Item -ItemType Directory -Force -Path $EvidenceRoot | Out-Null",
            utf8_setup,
            wrapper,
            f'$ObservedExit = Invoke-SageEvidence "PROBE" @("{native_exit}")',
            "$PolicyAfter = $ErrorActionPreference.ToString()",
            '$Value = (Get-Content -LiteralPath (Join-Path $EvidenceRoot "PROBE.stdout.txt") -Raw).Trim()',
            '$Diagnostic = (Get-Content -LiteralPath (Join-Path $EvidenceRoot "PROBE.stderr.txt") -Raw).Trim()',
            "$Codepoints = (($Value.ToCharArray() | ForEach-Object { [int][char]$_ }) -join ',')",
            "$ReceiptLines = @(",
            '    "EXIT=$ObservedExit"',
            '    "POLICY=$PolicyAfter"',
            '    "CODEPOINTS=$Codepoints"',
            '    "STDERR=$Diagnostic"',
            ")",
            f"Set-Content -LiteralPath {ps_literal(str(receipt))} -Value $ReceiptLines -Encoding UTF8",
            f"if ($ObservedExit -ne {native_exit}) {{ exit 91 }}",
            f'if ($PolicyAfter -ne "{caller_policy}") {{ exit 92 }}',
            "exit 0",
        ]
    )
    probe = tmp_path / "wrapper_probe.ps1"
    probe.write_text(script, encoding="utf-8-sig")

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    fields = dict(
        line.split("=", 1)
        for line in receipt.read_text(encoding="utf-8-sig").splitlines()
        if "=" in line
    )
    assert fields["EXIT"] == str(native_exit)
    assert fields["POLICY"] == caller_policy
    assert fields["CODEPOINTS"] == "84,252,114,107,231,101"
    assert "expected self-heal warning" in receipt.read_text(encoding="utf-8-sig")


def test_clean_machine_template_keeps_target_governance_separate() -> None:
    rendered = build_template(
        "operator",
        "PASS",
        "",
        r"C:\target repo",
        "fresh_prerequisites",
        "FAIL",
    )

    assert "CLEAN_MACHINE_INSTALLATION_VERDICT: PASS" in rendered
    assert "TARGET_REPOSITORY_GOVERNANCE_VERDICT: FAIL" in rendered
    assert "all compatible with a successful installation" in rendered


def test_new_installation_marker_takes_precedence_over_compatibility_alias() -> None:
    transcript = """\
CLEAN_MACHINE_INSTALLATION_VERDICT: FAIL
CLEAN_MACHINE_VERDICT: PASS
"""

    assert _clean_machine_installation_verdict(transcript) == "FAIL"


def test_legacy_installation_marker_remains_readable_during_transition() -> None:
    assert _clean_machine_installation_verdict("CLEAN_MACHINE_VERDICT: PASS\n") == "PASS"
    assert _clean_machine_installation_verdict("") == "MISSING"
