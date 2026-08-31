# Nexora SAGE React Runtime Gap Matrix

Date: 2026-06-15

This matrix documents which React signals are proven statically in v1 and which require v2 runtime proof.

## Matrix

| Signal Family | v1 Static Status | Runtime Gap | v1 Behavior |
|---|---|---|---|
| Hook dependency lists | Strong | Runtime closure values are not executed. | Emit static evidence and confidence. |
| Zustand store creation and selector use | Strong | Runtime mutation frequency is not observed. | Detect store/selector/no-selector/equality surfaces. |
| TanStack Query keys | Strong for literal/ref/dynamic classification | Runtime cache hit/invalidation behavior is not observed. | Classify key evidence and mutation/invalidation risks. |
| Next/RSC client-server boundary | Strong | Production hydration behavior is not observed. | Detect boundary contracts and mark runtime-sensitive risks. |
| Server Actions form contracts | Strong static/actionability layer | Real user pending/error UX is not observed. | Detect pending/error/input contract surfaces and lower actionability for reference surfaces. |
| Provider topology | Partial | Runtime provider reachability and consumer tree are not observed. | Static context/provider signals only. |
| Render cascade | Partial | Actual rerender cascade is not observed. | Surface likely memoization/stability risks; avoid certainty claims. |
| Lazy/Suspense | Partial | Lazy branch execution is not observed. | Mark lazy/runtime-sensitive surfaces as needing runtime proof. |
| Hydration mismatch | Partial | Browser hydration is not observed. | Emit risk signals only when static evidence exists. |
| Feature flags | Partial | Runtime flag values are not observed. | Detect flag surfaces; keep certainty bounded. |

## Release Rule

Runtime-sensitive findings should not be upgraded to deterministic certainty unless a runtime artifact links the observation back to Atlas.

## V2 Upgrade Target

The first runtime bridge should focus on:

1. React render/provider trace
2. Zustand mutation bridge
3. TanStack Query invalidation bridge
4. Route hit map

