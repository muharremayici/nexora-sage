# React Edge Case Coverage

Nexora SAGE uses this document and `config/react_edge_case_matrix.json` to keep the React nanometric claim honest.

## Claim Boundary

Nexora SAGE does not claim that every runtime-only React behavior is statically decidable. The claim is:

> Evidence-backed, confidence-ranked surgical analysis for React/TypeScript architecture, runtime-risk surfaces and AI-agent governance.

## Covered Edge Classes

- Runtime-only lazy/dynamic component boundaries.
- Feature flag and environment-gated render branches, marked with runtime-proof expectations.
- Provider topology and provider value stability.
- Next.js RSC, Server Actions, cache/revalidation and route input boundaries.
- TanStack Query mutation/invalidation drift.
- Form schema, server-action form and accessible error mapping gaps.
- Modal/portal focus and accessible-name contracts.
- Hydration-sensitive render values such as time, random and browser-only reads.
- Generated GraphQL/OpenAPI/Prisma client boundary policy.
- Package exports and workspace alias resolver behavior.

## Permanent Gates

```powershell
python tools/validate_react_edge_cases.py
```

This source-level validator is part of the shipped development test surface.
Target-repository analysis remains on the public `sage.py run` and MCP profiles;
the private aggregate `validate` command is not required for ordinary use.

## Runtime-Proof Semantics

Some edge cases are intentionally classified as `needs_runtime_proof`. That is not a weak claim; it prevents false certainty. Those findings should be paired with ContextOS, local telemetry, browser smoke or targeted integration tests before being treated as confirmed.

## V1 Positioning

For v1, Nexora SAGE is intentionally deepest in React/TypeScript and Next.js architecture. Python has strong AST-backed structural support. Java, Go and C# are part of the polyglot governance substrate, but their current claim is structural symbol/import extraction, not compiler-grade semantic governance.

ContextOS strengthens the React v1 claim by making every AI turn start from the live changed files, their first-ring halo, reasoning breadcrumbs and reverse upstream traces. Runtime-only React cases remain honest: they are surfaced, ranked and routed toward runtime proof instead of being overstated as statically settled.

Atlas/Genome also carry logic-first DNA for TypeScript/React symbols. Raw `dna` remains available, while `logic_dna` reduces framework wrapper noise such as `"use server"` and `useCallback` so equivalent logic can be tracked across React and Next.js shapes without overstating runtime-only behavior as statically proven.

## Fixture Evidence

React v1 readiness is fixture-gated through `config/react_fixture_matrix.json` and `tools/validate_react_fixtures.py`.
The required fixture set currently covers:

- Current workspace baseline.
- External Vite React fixture.
- External Next/App Router fixture.
- Router-oriented fixture artifacts.

This does not replace clean-machine onboarding evidence, but it does keep the React support claim from depending on only the host workspace.
