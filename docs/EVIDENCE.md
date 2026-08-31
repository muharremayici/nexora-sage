# Nexora SAGE Evidence

This is the short entry point for release evidence.

Use this document when you want to answer:

> What can Nexora SAGE honestly claim, and where is the proof?

## Current Public Claim

The allowed v1 claim is:

> Evidence-backed universal React web static architectural governance across the declared v1 static release-family taxonomy.

This claim is deliberately narrower than:

- full runtime React truth
- universal React runtime proof
- equal-depth polyglot analysis
- full SAST/AppSec replacement
- fully autonomous multi-agent software operation

## Fast Evidence Path

| Question                                      | Primary Evidence                                                                                           |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| What is the exact v1 claim boundary?          | `V1_RELEASE_BOUNDARY.md`                                                                                   |
| Which claims are proven, partial, or roadmap? | `CLAIMS_EVIDENCE_MATRIX.md`                                                                                |
| Where is the public release evidence bounded? | `V1_RELEASE_BOUNDARY.md` and `CLAIMS_EVIDENCE_MATRIX.md`                                                   |
| What React corpus supports the claim?         | `REACT_VALIDATION_CORPUS_V1_EVIDENCE.md`                                                                   |
| What React behavior families are covered?     | `output/.raw/react_corpus_saturation_report.json`, `output/.raw/react_fixture_family_taxonomy_report.json` |
| What security claims are safe to make?        | `SECURITY_CAPABILITY_MATRIX.md`                                                                            |
| What is still runtime roadmap?                | `REACT_RUNTIME_GAP_MATRIX.md`, `ROADMAP.md`                                                                |

## Machine-Readable Proof Artifacts

The most important generated artifacts are:

| Artifact                                                | Purpose                                   |
| ------------------------------------------------------- | ----------------------------------------- |
| `output/.raw/release_proof_bundle.json`                 | One-command release proof summary.        |
| `output/.raw/release_readiness.json`                    | Release readiness status and blockers.    |
| `output/.raw/claim_guard_validation.json`               | Public-claim guardrail.                   |
| `output/.raw/react_corpus_saturation_report.json`       | React required-family saturation.         |
| `output/.raw/react_fixture_family_taxonomy_report.json` | 80-family React fixture/support taxonomy. |
| `output/.raw/artifact_contract_validation.json`         | Artifact schema/contract proof.           |
| `output/.raw/layer_release_matrix.json`                 | Layer-to-evidence ownership map.          |

## Reproduce Product Operation

On a fresh machine, select the target repository explicitly:

```powershell
python sage.py init --plan-only --target-root "C:\path\to\repository"
python sage.py init --target-root "C:\path\to\repository"
python sage.py doctor --include-validate --quick --max-seconds 180
python sage.py run --target-root "C:\path\to\repository" --profile daily --projects MAIN
```

These commands reproduce installation and bounded repository operation. The
complete SAGE self-release proof is intentionally reserved for maintainers at a
frozen release boundary and is not part of normal product use.

## Interpretation Rules

- `release_proof_bundle` PASS means the local development evidence bundle is internally consistent.
- `claim_guard_validation` PASS means public wording stays within the allowed v1 claim.
- `react_universal_ready=true` requires every declared v1 static release family to be proven by executable contracts; it does not prove runtime-only behavior.
- The 80-family taxonomy defines the support universe, 34 release families define v1 static proof, and the 22-family corpus report provides real-repository calibration. These are complementary evidence sets.
- Clean distribution PASS proves the source package is clean and matches its sync-generated delivery fingerprint; it does not replace a fresh-machine execution transcript.
- External corpus evidence strengthens confidence, but the claim boundary is still governed by claim guard and release readiness.

## Evidence Style

Nexora SAGE should be described through evidence, not vibes.

Good:

> SAGE provides evidence-backed universal React web static architectural governance across its declared v1 static release-family taxonomy, backed by executable contracts, real-repository calibration, artifact contracts, release proof, and claim guard validation.

Avoid:

> SAGE statically understands every React runtime behavior.

Avoid:

> SAGE is a full security scanner or replacement for SAST.

Avoid:

> Every supported language has equal nanometric depth.
