# Before changing a store, find its consumers

**Question:** If we change Zustand's store core, where should a developer or
coding agent look next?

In a real, frozen Zustand checkout, SAGE located the `createStore` definition
and returned three dependent modules from its indexed dependency graph. We
checked those connections against the source. The result is a concrete review
starting point: inspect the public entry point and both React bindings before
changing the store implementation.

## Repository -> output -> decision

The target was [Zustand at commit b57db4f](https://github.com/pmndrs/zustand/tree/b57db4f86ef179285da216eeb291266da82c361c),
not today's upstream state. The analyzer was the published Nexora SAGE
**v1.4.0** source, used in a separate local installation. The question concerned
[`src/vanilla.ts`](https://github.com/pmndrs/zustand/blob/b57db4f86ef179285da216eeb291266da82c361c/src/vanilla.ts).

| Actual SAGE observation | Source check | Useful next decision |
| --- | --- | --- |
| `createStore`, lines 99-100 in `src/vanilla.ts` | The exported definition exists at that span. | Start at the definition, then read the implementation it delegates to. |
| `src/index.ts` depends on `vanilla.ts` | The entry point re-exports it. | Review the public export surface. |
| `src/react.ts` depends on `vanilla.ts` | The React binding imports `createStore`. | Review effects on the React-facing binding. |
| `src/traditional.ts` depends on `vanilla.ts` | The traditional binding also imports `createStore`. | Review the other binding rather than assuming one consumer. |

The query used SQLite-backed dependencies with depth **2**. These are three
returned graph dependents, not a promise that every affected consumer, type
contract, dynamic reference or downstream application has been found.
Middleware also imports types from the core; changing `StoreApi` requires
additional type-contract review, not just this three-file list.

This is orientation for a proposed change. We did not edit Zustand or claim
that its store is defective. SAGE supplied grounded information; the developer
still owns scope, compatibility checks and the decision to change code.

## What ran

An explicit `MAIN` analysis selected the `src` project and indexed **15**
TypeScript source files. The complete target integrity inventory contained
**144** files. Those denominators describe different scopes: this was not a
whole-repository assessment. The demo, starter and repository-root test suites
were outside the selected Atlas scope.

SAGE's analysis process completed, but its **quality gate was FAIL**. The four
enforced failures concerned React capability detection/partial capability and
proof-obligation completeness. They were not adjudicated as Zustand defects in
this case study. A successful command does not turn that gate green.

The target's files and clean Git status were unchanged after analysis and
queries. No target dependencies were installed, no target configuration was
executed, and no native tests, React component or browser were run. SAGE's AST
parser used TypeScript 5.9.3; this was not the target's native TypeScript 6.x
compiler check. The local Node runtime was outside the release CI reference
major, which setup disclosed rather than treating as equivalent CI coverage.

## A test boundary worth knowing

`get_test_impact` returned an empty candidate list for this file. That is **not**
evidence that Zustand has no relevant tests. Source inspection found
`tests/vanilla/basic.test.ts` and `tests/vanilla/subscribe.test.tsx`, importing
`zustand/vanilla`. The repository's TypeScript/Vitest configuration maps that
package alias to `src/vanilla.ts`; the Vitest configuration names `tests` as its
test directory.

Those tests were found during manual verification, **not returned by SAGE**.
The selected Atlas excludes that directory, and the live fallback inspects
co-located siblings rather than this separate alias-bound suite. We retained
this acquisition limitation for follow-up instead of presenting an empty list
as complete test coverage. Review native suites before relying on a change.

## Try the same question

The [short demo](ZUSTAND_CHANGE_IMPACT_DEMO.md) shows the actual bounded output
and the commands/tools used. The [public-safe evidence projection](zustand_change_impact_evidence.json)
records the exact target, analyzer, generation, selected file hashes and limits.
It is an editorial projection, not a new product artifact or release gate.

One verified example does not measure accuracy, time saved, token savings or
runtime correctness. It demonstrates a practical use: **turn a proposed core
change into a source-grounded list of places to inspect next**. Zustand does
not endorse SAGE through this evaluation.
