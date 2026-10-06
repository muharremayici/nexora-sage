# A dynamic route is not a domain layer

An architecture analyzer should help you avoid an unnecessary change, not just
produce more warnings. This source-checked Corpus example shows a naming
collision that previously produced a misleading SAGE architecture finding.

## The example

The preserved subject is [Dub](https://github.com/dubinc/dub) at commit
`c3b5d4975e47982149469797b268a39322230af6`, not today's upstream repository.
Its `WEB` project contains
`app/[domain]/[key]/inspect/card.tsx`; the repository-relative path is
`apps/web/app/[domain]/[key]/inspect/card.tsx`.

The file renders a link-inspector card and imports UI components. The preserved
SAGE v1.0.4 Audit labeled that import `domain_ui_leaks` and suggested an
architecture-boundary refactor.

The conclusion was not justified by the path. In Next.js, a bracketed folder is
a [dynamic route segment](https://nextjs.org/docs/app/api-reference/file-conventions/dynamic-routes).
The word `domain` in `[domain]` is not evidence of a Domain-Driven Design
business-logic layer. The source inspection corroborates this particular
misclassification; it does not certify every import in the file as correct.

## What changed in SAGE

The bounded v1.0.5 acceptance checked two independent protections:

- Layer hints match complete path segments. `[domain]` does not match `domain`.
- Architecture rules consume the exact project's applicable policy. A Next.js
  profile does not acquire Clean/FSD enforcement from a competing heuristic
  score. Oracle recognition alone does not grant enforcement authority.

The v1.4.0 static demonstration retains these protections. On the released
candidate, the existing resolver produced:

```text
app/[domain]/[key]/inspect/card.tsx -> unknown
src/domain/model.ts -> domain/logic
```

`unknown` means this path does not establish a doctrine layer. It is not a
clean-repository verdict. The second path is a synthetic positive control;
its classification alone would still not authorize a violation or a refactor.

Three existing contract tests also check Next/Clean policy separation,
preservation of applicable checks, and rejection of waiting or stale policy.
See the [reproducible static demo](NEXT_DYNAMIC_ROUTE_DEMO.md).

## Evidence and scope

The preserved v1.0.4 `WEB` Audit contains 210 candidates in the two reviewed
architecture-rule families: 131 `domain_ui_leaks` and 79
`hexagonal_layer_violation`. Of these, 53 candidate rows refer to 18 unique
files whose paths contain a `[domain]` segment.
Those are candidate counts, **not 210 manually confirmed false positives**.

The historical bounded acceptance checked those paths against the generalized
resolver and applicable Next policy. The current editorial review independently
rechecked the preserved report identities, counts, selected source file and
bounded static behavior. It did not refresh Atlas or rerun the Dub pipeline.

The [evidence projection](next_dynamic_route_evidence.json) records the pinned
target, one source fingerprint, preserved run fingerprints and current demo
scope. Aggregate run exports are not bundled; this projection alone does not
independently reproduce the original full analysis. No third-party source code
or raw target report is redistributed here.

## What this example does not prove

It does not measure Corpus-wide precision or recall, prove a runtime defect,
execute Dub's tests, verify effective native lint policy, assert that Dub is
clean, or assess its current upstream release. It implies no endorsement by
Dub's maintainers. The current static smoke is not a fresh end-to-end scan.

The practical lesson is narrow: keep the finding, its source context and rule
applicability together before asking an agent to change code.

To analyze your own repository, use the [Quickstart](../QUICKSTART.md) and the
[AI Agent HITL Runbook](../AI_AGENT_HITL_RUNBOOK.md). SAGE remains a context and
evidence provider, not an agent or a substitute for your linter and test runner.
