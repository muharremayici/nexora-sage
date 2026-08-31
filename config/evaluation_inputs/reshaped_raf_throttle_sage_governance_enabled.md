You are evaluating one frozen repository task under SAGE governance. Work read-only.

Repository root: C:\tmp\sage-holdout-reshaped-1bb445b6
Frozen commit: 1bb445b6f442071694f7aae5e1d3f0bb8eda2feb
Target: packages/utilities/src/helpers/rafThrottle.ts#rafThrottle

Task: Determine whether changing the callable signature of rafThrottle is a file-local private refactor. Enumerate the minimum source-grounded static impact scope needed to make such a signature change safely.

SAGE bounded context from frozen source git:bd927696b2d50add815928722b0eb46e9e46de35 and runtime TypeScript 5.9.3:
- Atlas dependency coverage: observed=543, degraded=0, unavailable=0, warnings=0.
- File-level Blast Radius for packages/utilities/src/helpers/rafThrottle.ts: direct_dependents=2, transitive_dependents=263, total_impact_score=133.5.
- The graph resolves both relative/alias imports and the exported workspace subpath @reshaped/utilities/internal.
- Relevant graph evidence includes packages/utilities/src/helpers/index.ts, packages/utilities/src/internal.ts, packages/utilities/src/flyout/Flyout.ts, packages/reshaped/src/components/Carousel/Carousel.tsx, packages/reshaped/src/hooks/_internal/useFadeSide.ts, and packages/utilities/src/helpers/tests/rafThrottle.test.ts.
- Claim boundary: these counts are file-level graph reachability. Barrel re-exports can make transitive reachability much broader than exact symbol or edit scope. The 263 count is not an assertion that 263 files require edits.

Governance requirements:
- Treat repository source at the frozen commit as engineering authority; SAGE context is bounded decision support, not a substitute for source verification.
- Distinguish file-level reachability, exact symbol usage, and files that actually require edits.
- Do not call the change file-local when exports, tests, or consumers make the callable contract non-local.
- Report missing or runtime-only knowledge as uncertainty; do not convert absence of evidence into PASS.
- Do not mutate files, expand scope silently, or claim runtime/product correctness.
- Preserve provenance for every scope item and recommend the smallest sufficient validation boundary.

Return exactly these fields in plain text:
- classification: file_local_private_refactor | not_file_local_private_refactor | inconclusive
- minimum_static_impact_scope: one repository-relative path per line, with the relevant symbol or role
- evidence: exact repository-grounded observations
- uncertainty: facts not established by static inspection
- recommendation: one bounded recommendation
- mutation_performed: false
- tool_call_count: your observed tool-call count
- correction_loop_count: your observed correction-loop count

Do not modify the repository. Do not inspect SAGE source, registries, prior evaluation results, or other agent output beyond this supplied governance envelope.
