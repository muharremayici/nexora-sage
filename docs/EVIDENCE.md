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

For direct user value, read [Before changing a store, find its consumers](case-studies/ZUSTAND_CHANGE_IMPACT_CASE_STUDY.md), its [actual bounded demo](case-studies/ZUSTAND_CHANGE_IMPACT_DEMO.md) and [evidence projection](case-studies/zustand_change_impact_evidence.json). This current-version analysis demonstrates dependency/symbol orientation, not a clean repository or complete test selection.

For secondary calibration evidence, read [A dynamic route is not a domain layer](case-studies/NEXT_DYNAMIC_ROUTE_CASE_STUDY.md), its [static contract demo](case-studies/NEXT_DYNAMIC_ROUTE_DEMO.md) and [evidence projection](case-studies/next_dynamic_route_evidence.json). Historical observations and current checks remain distinct; neither case is a precision measurement.

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

### Import finding source and policy context

The development Hexagonal JS/TS finding lane adds source-bound captured static
import record sets and the exact applicable SAGE path-model policy to Audit,
SQLite queues and agent directives. Named, default, namespace and side-effect
top-level imports are supported; other forms and legacy metadata remain explicit
unavailable context. Repeated records are not distinct source occurrences.
Record caps disclose partial detail without removing ordinary findings.

The same development transport additionally covers the existing declared-layer
import restrictions with explicit v2 evidence, preserving Hexagonal v1.
It records the consumed restriction row, first matched fragment and source
label, language/exemption decision and applicable project policy. The selected
SQLite finding message must match the projected record; a caller's new hash
alone is not producer evidence. Existing TypeScript-only restriction scope is
not expanded to JavaScript, and no target layer is invented for package imports.

Existing cross-module and own-API findings can also carry development v3
evidence for the consumed module container, raw alias/path criterion and
winning API-entry policy/exemption. Consumers verify current relevant configuration
and the complete selected SQLite finding without re-discovering live topology.
Source-module matching supports exact contiguous multi-component containers;
first-match and anchored single-component behavior are retained. The declared
alias/container prefix must start the raw import specifier; embedded alias text
in a foreign specifier does not establish module ownership. These repairs do
not add dot-root inference or change the existing literal API criteria.
Earlier evidence versions and source/policy verification remain in place;
it does not prove package exports or correctness of every architectural inference.

The binding uses the selected Atlas/source snapshot and current finding generation.
It does not establish individual import locations, independently resolved target
files, live-source freshness, target-native effective policy, runtime execution,
causal priority or safe edits. This additive development work is not a new public
release claim or an end-to-end precision measurement.

Nexora SAGE should be described through evidence, not vibes.

Good:

> SAGE provides evidence-backed universal React web static architectural governance across its declared v1 static release-family taxonomy, backed by executable contracts, real-repository calibration, artifact contracts, release proof, and claim guard validation.

Avoid:

> SAGE statically understands every React runtime behavior.

Avoid:

> SAGE is a full security scanner or replacement for SAST.

Avoid:

> Every supported language has equal nanometric depth.
