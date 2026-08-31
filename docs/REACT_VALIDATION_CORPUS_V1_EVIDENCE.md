# Nexora SAGE React Validation Corpus v1 Evidence

Date: 2026-06-15

This document is the public-facing evidence summary for the React v1 validation corpus. It condenses the longer internal audit logs into a release-oriented proof record.

## Claim Boundary

Nexora SAGE v1 is defensible as a React/TypeScript architectural governance specialist when the claim is scoped to:

- Static compiler evidence
- Atlas-backed import, symbol, route, state and dependency topology
- React edge-case fixtures
- Confidence-ranked uncertainty
- External repository validation
- Manual true-positive and false-positive review

It is not a claim that every runtime-only behavior is statically decidable. Runtime tracing remains a v2 capability.

## Corpus Coverage

| Family                            | Repositories Used                                                                              | Evidence Value                                                                                                   |
| --------------------------------- | ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Large Next.js and SaaS monorepos  | `cal.com`, `dub-main`, `documenso-main`, `formbricks-main`, `twenty-main`, `trigger.dev-main`  | Real product routing, server actions, forms, auth, dashboard and domain boundaries.                              |
| Large mixed React/Python products | `posthog-master`, `sentry-master`                                                              | Stress-tests generated clients, read-only query semantics, Python plus React workspace scale and long full runs. |
| CMS, commerce and admin systems   | `payload-main`, `medusa-develop`, `appsmith`, `saleor-dashboard`                               | Plugin/admin/public API surfaces, migrations, workflow exports and framework-runtime contracts.                  |
| React libraries and UI tooling    | `zustand-main`, `react-hook-form-master`, `query-main`, `material-ui-master`, `storybook-next` | Library public exports, type-level surfaces, docs/demo actionability and fixture-like source patterns.           |
| Canvas/editor state               | `excalidraw-master`                                                                            | Non-SaaS complex interaction/state surface with dense UI topology.                                               |
| Reference and starter apps        | `vite-react-ts-starter`, `realworld-react-fsd`, `create-t3-turbo`, `next-enterprise`           | Small, repeatable sanity surfaces for onboarding and fixture comparisons.                                        |

## Representative Runs

| Repository               | Category                   | Result | Key Evidence                                                                                                                |
| ------------------------ | -------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------- |
| `zustand-main`           | Zustand state library      | PASS   | Selector, no-selector and equality-function signals were manually matched to source tests.                                  |
| `react-hook-form-master` | Complex forms library      | PASS   | `useForm`, `register` and `handleSubmit` evidence was manually matched to a real nested form app.                           |
| `query-main`             | TanStack Query             | PASS   | Literal, identifier and dynamic query-key lanes were manually checked against source examples.                              |
| `documenso-main`         | Documents SaaS             | PASS   | tRPC query/mutation call chains and route-helper false-positive behavior were reviewed and turned into regression coverage. |
| `formbricks-main`        | Forms and survey SaaS      | PASS   | Dynamic test import dead-code false positive was confirmed and fixed.                                                       |
| `dub-main`               | Modern SaaS dashboard      | PASS   | Large Next.js route/action/form/context surface was indexed.                                                                |
| `payload-main`           | CMS/admin/plugin system    | PASS   | Post-Atlas Oracle reached `PROPOSE_SEAL` on a large CMS/admin/plugin repository.                                            |
| `medusa-develop`         | Commerce/framework runtime | PASS   | Migration, workflow and fixture contract surfaces were calibrated as framework/runtime surfaces.                            |
| `material-ui-master`     | UI component library       | PASS   | Public exports, locale exports, docs/demo and codemod fixture surfaces were manually reviewed.                              |
| `storybook-next`         | Storybook/tooling monorepo | PASS   | Proof-obligation zero-transition-subject semantics were corrected and validated.                                            |
| `twenty-main`            | CRM/admin monorepo         | PASS   | Large domain/frontend topology exposed and fixed a disappearing-source stability edge case.                                 |
| `excalidraw-master`      | Canvas/editor              | PASS   | Workspace-aware React preflight and cache contract drift behavior were validated.                                           |
| `infisical-main`         | Security dashboard         | PASS   | Auth, API client, Zod, form, TanStack and Zustand signals were matched to source.                                           |
| `posthog-master`         | Analytics product          | PASS   | Generated API client contract surfaces were calibrated; dead-code pressure fell after registry correction.                  |
| `sentry-master`          | Observability product      | PASS   | Read-only TanStack Query files no longer create state transition obligations.                                               |

## Corrections Promoted From Corpus

| Source Case                                     | Correction                                                                                                     | Regression Proof                                                      |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| TanStack Query static literal keys              | Static literals inside query-key arrays are no longer downgraded to dynamic keys.                              | `validate_zustand_selector_contract.py` static query literal fixture. |
| React Hook Form arbitrary `.register()` methods | Form evidence is tied to `useForm` bindings, reducing arbitrary-method false positives.                        | Source guard plus form-binding fixture.                               |
| Documenso read-only route helper                | Read-only route factories are not mutation surfaces unless env-sensitive route evidence exists.                | `NextBoundaryAnalyzerTests`.                                          |
| Formbricks dynamic test import                  | Named exports consumed through dynamic import destructuring in tests are not high-confidence dead exports.     | `DeadCodeDynamicImportTests`.                                         |
| MUI docs/demo and codemod fixtures              | Technical signals remain visible, but docs/demo/test-support surfaces are not treated like production defects. | Doctrine registry plus contract tests.                                |
| Medusa migration/workflow surfaces              | Framework runtime exports are protected from high-confidence dead-code classification.                         | Doctrine registry plus contract tests.                                |
| PostHog generated clients                       | Generated API/schema/client outputs are contract surfaces.                                                     | Generated API client registry test.                                   |
| Sentry read-only query semantics                | Read-only `useQuery` files are data surfaces, not state transition subjects.                                   | `StateFlowScannerContractTests`.                                      |
| Frozen genome contract drift                    | Frozen cache lift validates genome contract version before reuse.                                              | Query-main quality rerun and Nuclear cache guard.                     |
| SQLite first-read schema warning                | The artifact store initializes SQLite schema before first `state_payloads` read.                               | `ArtifactStoreSQLiteContractTests`.                                   |

## Evidence Sources

- `docs/EXTERNAL_REPOSITORY_EVALUATION.md`
- `docs/REACT_EDGE_CASE_COVERAGE.md`
- `docs/REACT_RUNTIME_GAP_MATRIX.md`
- `config/react_fixture_matrix.json`
- `output/.raw/react_fixture_matrix_validation.json`
- `output/.raw/react_edge_case_validation.json`
- `tools/tests/test_engine_contracts.py`

## Evidence Retention Rule

Generated external-target artifacts under `output/` are runtime evidence and may
be purged during clean packaging. Durable corpus evidence must be promoted into
a versioned evidence summary, matrix or manifest before cleanup. Public release
claims should cite the promoted document plus the validator/fixture proof that
can regenerate or defend the claim.

## Release Verdict

The corpus supports the public claim:

> Nexora SAGE v1 is production-grade for core React web architectural governance and strongly validated across representative React ecosystem families.

The corpus does not support unqualified claims such as:

> Nexora SAGE statically proves every possible runtime React behavior.
