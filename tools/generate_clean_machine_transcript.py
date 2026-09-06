from __future__ import annotations

import argparse
import platform
import sys
from datetime import datetime
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import REPORTS_DIR, save_text_atomic

TRANSCRIPT_PATH = REPORTS_DIR / "clean_machine_setup_transcript.md"


def build_template(
    operator: str,
    verdict: str,
    evidence_note: str,
    target_root: str,
    machine_baseline: str,
    target_governance_verdict: str = "NOT_EVALUATED",
) -> str:
    now = datetime.now().isoformat(timespec="seconds")
    powershell_target = target_root.replace("'", "''")
    lines = [
        "# Clean Machine Setup Transcript",
        "",
        "This document is the evidence artifact for Phase 6 (Distribution hardening).",
        "",
        f"- Generated at: `{now}`",
        f"- Operator: `{operator}`",
        f"- Host OS: `{platform.platform()}`",
        f"- Machine baseline: `{machine_baseline}`",
        f"- Target repository: `{target_root}`",
        "",
        "## Scope",
        "",
        "- Target: clean-machine setup from public documentation and public product surfaces only",
        "- `fresh_prerequisites` means Python/Node/Git may have been installed immediately before SAGE; it remains clean-machine evidence.",
        "- This is not `bare_os_bootstrap` evidence unless SAGE itself provisions every prerequisite from an untouched OS.",
        "- Expected installation marker: `CLEAN_MACHINE_INSTALLATION_VERDICT: PASS`",
        "- `CLEAN_MACHINE_VERDICT` is retained as a compatibility alias for the installation verdict.",
        "- Target-repository governance is recorded separately and never determines whether SAGE installed correctly.",
        "",
        "## Prerequisite Record",
        "",
        "Record whether each prerequisite was pre-existing or installed during this session:",
        "",
        "```powershell",
        "python --version",
        "node --version",
        "git --version",
        "```",
        "",
        "## PowerShell Evidence Method",
        "",
        "Windows PowerShell 5 may convert redirected or merged native stderr diagnostics into `NativeCommandError` records.",
        "SAGE deliberately reserves stderr for diagnostics so MCP stdio is not corrupted. Capture streams separately, then print both into the transcript:",
        "",
        "```powershell",
        f"$TargetRoot = (Resolve-Path -LiteralPath '{powershell_target}').Path",
        "$EvidenceRoot = Join-Path $PWD \"clean-machine-evidence\"",
        "New-Item -ItemType Directory -Force -Path $EvidenceRoot | Out-Null",
        "$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)",
        "[Console]::InputEncoding = $Utf8NoBom",
        "[Console]::OutputEncoding = $Utf8NoBom",
        "$OutputEncoding = $Utf8NoBom",
        "$env:PYTHONUTF8 = \"1\"",
        "$env:PYTHONIOENCODING = \"utf-8\"",
        "function Invoke-SageEvidence([string]$Name, [string[]]$Arguments) {",
        "    $stdout = Join-Path $EvidenceRoot \"$Name.stdout.txt\"",
        "    $stderr = Join-Path $EvidenceRoot \"$Name.stderr.txt\"",
        "    $previousErrorActionPreference = $ErrorActionPreference",
        "    $exit = 127",
        "    try {",
        "        $ErrorActionPreference = \"Continue\"",
        "        & python -u sage.py @Arguments 1> $stdout 2> $stderr",
        "        $exit = [int]$LASTEXITCODE",
        "    } finally {",
        "        $ErrorActionPreference = $previousErrorActionPreference",
        "    }",
        "    Get-Content -LiteralPath $stdout | ForEach-Object { Write-Host $_ }",
        "    Get-Content -LiteralPath $stderr | ForEach-Object { Write-Host $_ }",
        "    Write-Host \"${Name}_EXIT=$exit\"",
        "    return $exit",
        "}",
        "```",
        "",
        "Replace `<TARGET_REPOSITORY>` before running when the generated template still contains that placeholder.",
        "",
        "## Command Log",
        "",
        "### 1. Read-only plan",
        "",
        "```powershell",
        "$PlanExit = Invoke-SageEvidence \"PLAN\" @(\"init\", \"--plan-only\", \"--target-root\", $TargetRoot)",
        "```",
        "",
        "### 2. Target-aware initialization",
        "",
        "```powershell",
        "$InitExit = Invoke-SageEvidence \"INIT\" @(\"init\", \"--target-root\", $TargetRoot)",
        "```",
        "",
        "### 3. Repository-scope doctor",
        "",
        "```powershell",
        "$DoctorBeforeExit = Invoke-SageEvidence \"DOCTOR_BEFORE\" @(\"doctor\", \"--include-validate\", \"--quick\", \"--max-seconds\", \"180\")",
        "```",
        "",
        "### 4. Release-level installation proof",
        "",
        "```powershell",
        "$InstallProofExit = Invoke-SageEvidence \"INSTALL_PROOF\" @(\"install-proof\", \"--level\", \"release\", \"--skip-deps\", \"--target-root\", $TargetRoot, \"--projects\", \"MAIN\")",
        "```",
        "",
        "### 5. Structured installation-proof identity check",
        "",
        "This check uses native PowerShell JSON handling. It deliberately avoids inline `python -c` quoting.",
        "",
        "```powershell",
        "$ProofPath = Join-Path $PWD \"output\\.raw\\installation_proof.json\"",
        "$Proof = Get-Content -LiteralPath $ProofPath -Raw -Encoding UTF8 | ConvertFrom-Json",
        "$ExpectedTarget = [IO.Path]::GetFullPath($TargetRoot).TrimEnd('\\')",
        "$ActualTarget = [IO.Path]::GetFullPath([string]$Proof.target_scope.target_root).TrimEnd('\\')",
        "$TargetMatches = [StringComparer]::OrdinalIgnoreCase.Equals($ExpectedTarget, $ActualTarget)",
        "$ProjectsMatch = [StringComparer]::OrdinalIgnoreCase.Equals([string]$Proof.target_scope.projects, \"MAIN\")",
        "$StepRows = @($Proof.steps)",
        "$StepsPassed = $StepRows.Count -gt 0 -and @($StepRows | Where-Object { -not $_.passed }).Count -eq 0",
        "$InstallProofStructuredPass = ([string]$Proof.summary.status -eq \"PASS\") -and $TargetMatches -and $ProjectsMatch -and $StepsPassed",
        "Write-Host \"INSTALL_PROOF_STRUCTURED_PASS=$InstallProofStructuredPass TARGET_MATCH=$TargetMatches PROJECTS_MATCH=$ProjectsMatch STEPS_PASS=$StepsPassed\"",
        "```",
        "",
        "### 6. Post-analysis doctor",
        "",
        "```powershell",
        "$DoctorAfterExit = Invoke-SageEvidence \"DOCTOR_AFTER\" @(\"doctor\", \"--include-validate\", \"--quick\", \"--max-seconds\", \"180\")",
        "```",
        "",
        "### 7. Exit summary",
        "",
        "```powershell",
        "Write-Host \"PLAN_EXIT=$PlanExit INIT_EXIT=$InitExit DOCTOR_BEFORE_EXIT=$DoctorBeforeExit INSTALL_PROOF_EXIT=$InstallProofExit INSTALL_PROOF_STRUCTURED_PASS=$InstallProofStructuredPass DOCTOR_AFTER_EXIT=$DoctorAfterExit\"",
        "```",
        "",
        "## Notes",
        "",
        f"- {evidence_note}" if evidence_note else "- [record blockers, dependency installs, environment differences]",
        "",
        "## Verdict",
        "",
        f"CLEAN_MACHINE_INSTALLATION_VERDICT: {verdict}",
        "",
        f"TARGET_REPOSITORY_GOVERNANCE_VERDICT: {target_governance_verdict}",
        "",
        f"CLEAN_MACHINE_VERDICT: {verdict}",
        "",
        "Set the installation verdict to `PASS` only when every exit code above is zero, `INSTALL_PROOF_STRUCTURED_PASS=True`, and no unpublished or local fallback was used.",
        "Record target governance independently from the target's structured analysis result. `PASS`, `FAIL`, `INCOMPLETE_EVIDENCE`, and `NOT_EVALUATED` are all compatible with a successful installation when reported honestly.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate clean-machine setup transcript template.")
    parser.add_argument("--operator", default="HITL", help="Operator identifier for transcript.")
    parser.add_argument(
        "--verdict",
        choices=["PENDING", "PASS", "FAIL"],
        default="PENDING",
        help="Machine-readable clean-machine verdict marker.",
    )
    parser.add_argument("--evidence-note", default="", help="Optional one-line evidence note for the transcript.")
    parser.add_argument(
        "--target-root",
        default="<TARGET_REPOSITORY>",
        help="Repository path recorded in the transcript template.",
    )
    parser.add_argument(
        "--machine-baseline",
        choices=["bare_os", "fresh_prerequisites", "preprovisioned"],
        default="fresh_prerequisites",
        help="Clean-machine prerequisite baseline classification.",
    )
    parser.add_argument(
        "--target-governance-verdict",
        choices=["PASS", "FAIL", "INCOMPLETE_EVIDENCE", "NOT_EVALUATED"],
        default="NOT_EVALUATED",
        help="Separate target-repository governance result; it does not control installation PASS.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing transcript if present.")
    args = parser.parse_args()

    if TRANSCRIPT_PATH.exists() and not args.force:
        print(f"[INFO] Transcript already exists: {TRANSCRIPT_PATH}")
        print("[INFO] Use --force to overwrite.")
        return 0

    save_text_atomic(
        TRANSCRIPT_PATH,
        build_template(
            args.operator,
            args.verdict,
            args.evidence_note,
            args.target_root,
            args.machine_baseline,
            args.target_governance_verdict,
        ),
    )
    print(f"[OK] Wrote transcript template: {TRANSCRIPT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
