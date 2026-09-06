# External Repository Evaluation

This document projects `config/external_repository_evaluation_contract.json` for human review. It defines evaluation authority; it does not execute models or repositories.

## Corpus Roles

| Role | May Tune SAGE? | Independent Generalization Evidence? | Meaning |
|---|---:|---:|---|
| Calibration | Yes | No | Known examples used to understand behavior and tune rules. |
| Regression | Yes | No | Known examples rerun to prevent previously fixed behavior from returning. |
| Holdout | No, before result freeze | Yes | A repository verified as unseen before the run. |
| Blind holdout | No | Yes | Repository, task and actor plan are sealed before execution. |
| Longitudinal | Yes | No | A known repository followed over time for drift and operational value. |

Calibration is not independent proof. The existing React corpus remains valuable calibration and regression evidence, but its repository count must not be reused as a blind-generalization score.

A holdout stops being unseen after its result is inspected. It may then become regression evidence; the next independent evaluation needs a new unseen subject.

When a holdout exposes a SAGE defect, post-fix generalization uses an ordered pair. First, a **near-neighbor** unseen repository tests the same failure family without target-specific tuning. Second, a **topology-divergent** unseen repository tests the repaired invariant under materially different repository structure, build ownership or product topology. Neither stage alone proves universality, and both retain the normal language, task, evidence and promotion boundaries.

After both stages freeze, their aggregate review is recorded in the same evaluation registry. The review names the originating regression, references distinct stage subjects, preserves each frozen outcome, lists only shared supported invariants, records non-promoted claims and open evidence gaps, and makes the next holdout decision explicit. Completion does not create an automatic third-repository requirement; another unseen subject is selected only when a declared evidence gap identifies the needed stratum or claim boundary.

## Language Authority

Language claim level limits evaluation authority.

Claim level is a ceiling, not an entitlement. A test can produce `PASS` authority only from the intersection of target applicability, language claim level, separately active capability evidence and the declared task domain. For example, a deep-specialist language does not imply support for every framework written in that language.

- `deep_specialist` may be evaluated for structural, AST, framework-semantic and declared static-governance behavior.
- `ast_strong` may be evaluated for structural and AST behavior. Framework, compiler-grade type-flow and runtime results remain exploratory or unsupported.
- `structural` may be evaluated for files, packages, symbols and imports. It cannot produce semantic or runtime PASS authority.

The language level comes from `config/polyglot_capabilities.json`; this contract does not maintain a second per-language list.

## Outcomes

- `PASS` applies only to the declared supported domain.
- `FAIL` remains in the result set and must not be tuned away after inspection.
- `INCONCLUSIVE` means evidence was missing, stale, conflicting or insufficient.
- `UNSUPPORTED` means the task exceeds the active claim.
- `NOT_APPLICABLE` means the behavior is absent from that subject.
- `INVALID_SUBJECT` means identity or provenance is insufficient for evaluation.

UNSUPPORTED and NOT_APPLICABLE are not PASS. Missing measurements use `not_available` with a reason; they are never converted to zero.

## Selection And Promotion

Every subject records repository identity, commit or content fingerprint, prior SAGE exposure and selection strata. Failed, unsupported and inconclusive subjects remain visible.

Repository count alone does not prove universality. Claim expansion requires multiple predeclared holdouts, representative strata, negative and boundary cases, source-grounded manual sampling, fixture and validator promotion, claim-guard alignment and human release authority.

Every evaluation task also declares the real failure mode it represents, its user-relevance hypothesis, expected evidence and success criteria. Mechanical contract conformance can prove that SAGE behaved as specified; it cannot by itself prove that the specification solves an important user problem.

## Finding-Quality Sampling

An unseen repository selected for actionable finding-quality evidence requires a predeclared sampling plan before repository selection. `TP`, `FP` and `FN` adjudication uses independent source truth; SAGE output cannot certify itself. `UNKNOWN`, `UNSUPPORTED` and `NOT_APPLICABLE` remain visible and cannot satisfy a positive quality claim.

Pre-registration has two ordered truth boundaries. Before candidate search, the registry freezes the task, authority ceiling, finding families, risk strata, required repository shape and the method that will establish false-negative truth. After one repository identity is selected and its source is frozen, concrete FN truths are recorded from independent source evidence before any SAGE finding or analysis command runs. This avoids both target-shaped evaluation design and fabricated claims about source that has not yet been inspected.

Candidate discovery remains metadata-only until repository identity and commit are frozen. The registry records rejected candidates as well as the selected identity so selection does not become invisible cherry-picking. If a search surface exposes package, source or finding content before identity freeze, that candidate is excluded from source-blind holdout eligibility rather than being silently reused. Live candidate identity, metadata hypotheses and transition state remain authoritative only in `config/external_repository_evaluation_registry.json`; SAGE execution remains forbidden until concrete FN truths are frozen.

The source-truth transition is explicit rather than inferred from a non-empty list. Every declared finding family must have exactly one schema-valid `APPLICABLE` or `NOT_APPLICABLE` truth, the selected subject must move to `identity_verified`, and only then may the candidate's SAGE execution gate open. Repository shape, license, generated boundaries and test surface are source facts recorded on that subject; this document does not duplicate the live candidate.

Sampling is deterministic, risk-stratified and adaptive rather than a copied fixed count. Blocking and high-severity findings are reviewed exhaustively inside the declared task scope. Remaining findings are selected across finding family, engine, source file, repository stratum, language authority and evidence state. False-negative probes are declared from frozen source truth before SAGE findings are inspected. If the adaptive safety ceiling prevents required coverage, the result is `INCONCLUSIVE`, not sampled `PASS`.

The SAGE-on/off variants and common task metrics remain owned by `config/agent_harness_contract.json`. This contract adds repository-specific identity, applicability and epistemic outcome rules without duplicating the future evaluation runner.

## Factorial Mechanics

A factorial plan binds every cell to the same frozen repository, task, actor and model identity. The planned condition is the only intended difference: no SAGE, SAGE context only, or SAGE governance enabled. Runner configuration and the exact SAGE source identity must be frozen before any cell starts; otherwise execution remains forbidden.

Factorial mechanics on known regression are not independent proof. A known subject may test capture, isolation and comparison mechanics, but it cannot prove generalization, causal product value or token savings. Missing measurements remain `not_available` with a reason, and contamination or unequal cell inputs invalidate the comparison rather than becoming a favorable result.
