# Roadmap

This roadmap is intentionally conservative and registry-backed.

The sources of truth are:

```txt
config/capability_registry.json
config/roadmap_phase_registry.json
config/sage_work_item_registry.json
```

Generate the machine-readable and human report with:

```powershell
python -m tools.engines.capability_roadmap_report
```

Validate the roadmap contract with:

```powershell
python .\tools\validate_capability_registry.py
```

Roadmap maintenance checks run in the private development workflow.

The roadmap is not a wishlist. A roadmap capability becomes release-claimable
only after it has engines, artifacts, validators, release-proof coverage and
claim-guard wording.

## Current Candidate And Claim Baseline

The active `1.6.1` candidate is **CI Evidence Provenance And Shadow Environment
Diagnostics**. Its single reviewed maintenance delivery strengthens tested-source
and CI receipt matching, captures bounded environment observations in actual
jobs and compares those observations in shadow mode. All four matrix suites
and the main reference suite remain enabled. This is a candidate scope, not a
publication decision, readiness verdict, test-reuse permission or measured
speedup claim.

The published `1.6.0` delivery remains historical: its six reviewed rows covered
finite callback source context, three import source/policy evidence lanes,
multi-component module-root matching and existing preparation maintenance.

The existing React/TypeScript static-governance claim profile remains active:
no third-party plugin runtime, runtime/schema/value proof, activation or
reachability proof, or complete-graph proof is included in this cut. The
original v1 claim baseline is preserved, not expanded by a new version label.

## Future Planning Phase

`1.7.0` is **Dormant React And Extension Continuity** in the phase registry.
It holds unactivated React, static-governance and extension intentions apart
from the later polyglot phase. It is future planning only, not a selected public
release, feature commitment, capability activation or license change.

Capability `target_release` and `maturity` belong to the capability registry;
historical debt targets and actual `delivered_release` belong to the work-item
registry. Completing a subset does not activate its wider roadmap capability.
The third-party plugin runtime remains dormant and outside the current claim.

## Historical Capability Intent

The following slots preserve the original capability plan. They are not the
scope actually shipped under each version, and they must not override current
registry phases, capability maturity or delivered-work evidence. In particular,
the original plugin intent for `1.5.0` was not activated by the finite candidate.

- `1.0.0`: bounded, evidence-backed React/TypeScript architectural governance on a polyglot substrate, including DNA-driven capability activation, advisory immutability evidence and a minimal CI gate.
- `1.1.0`: proof and agent-surface hardening: target repository proof envelopes, bidirectional scoped SAGE/target-repository projections, documentation-to-code freshness, local governance telemetry, deterministic source-release agent bootstrap and React fixture/taxonomy promotion.
- `1.2.0`: cage and principle calibration: privacy, N+1, resilience, cognitive-load and advisory engineering wisdom packs.
- `1.3.0`: React topology intelligence: provider topology, auth/cache/state graphs, route-data/mutation graphs, state topology, server-state graphs and bundle intelligence.
- `1.4.0`: operational evidence and developer surfaces: external evidence adapters, IDE/LSP surfaces, standard Python package distribution, AST daemon performance work, shadow variation labs and bounded agent execution foundations.
- Original `1.5.0` intent: trusted plugin runtime foundation, not activated; executable third-party plugin governance still requires mature sandbox, trust, artifact and claim-boundary contracts.
- `2.0.0`: polyglot semantic expansion. The goal is to move non-React languages from structural substrate toward deeper language-normalized analysis.
- `2.5.0`: runtime-aware and distributed-system governance after the polyglot semantic layer exists.
- `3.0.0`: bounded multi-agent coordination and private learning loops.

This keeps 1.x focused on React dominance while reserving 2.0.0 as the first
major polyglot expansion line.

Current v1 capability groups:

| Capability                       | Boundary                                                                                                                           |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Repository discovery             | Discovers workspace shape, hypotheses and policy-backed project DNA; final architecture confidence belongs to post-Atlas evidence. |
| Atlas and sequencing             | React/TypeScript is deep; non-React languages are structural unless separately proven.                                             |
| Architecture governance          | Oracle proposes architecture; doctrine sealing remains human-governed.                                                             |
| React surgical intelligence      | Bounded source-backed React/TypeScript architectural governance; not full runtime/browser or complete-graph proof.                   |
| Dead code and public contracts   | Protects known public surfaces; runtime-only reflection remains confidence-ranked.                                                 |
| Dependency health                | Static dependency graph and impact analysis; runtime dependency flow is post-2.0.0.                                                |
| Test impact                      | Maps likely impacted tests; does not prove coverage completeness.                                                                  |
| Merge and variation intelligence | Provides architectural merge evidence and plans; generated mutations still require HITL/safety gates.                              |
| ContextOS live focus             | Distills live repository signals for agents; not a replacement for full pipeline proof.                                            |
| Agent surface                    | Provides deterministic AI-agent context and governance; autonomous changes still need approval.                                    |
| SQLite artifact store            | SQLite is primary runtime artifact store with JSON shadow export.                                                                  |
| Plugin extension foundation      | Defines plugin/capability contracts; does not load third-party executable plugin code in v1.                                       |
| Security boundary                | Covers AI-agent safety and local boundary controls; not a full SAST/AppSec scanner.                                                |
| Capability activation planner    | Project DNA selects relevant capability-bound steps in non-release profiles; forced and release runs remain complete.              |
| Immutability and purity cage     | Provides policy-backed advisory React mutation evidence; it is not runtime proof.                                                  |
| CI/CD gate surface               | Provides a minimal read-only GitHub Actions contract gate; not hosted CI governance.                                               |
| Release proof                    | Proves the scoped release claim only; claim guard blocks wider unsupported claims.                                                 |

## Historical Capability Intent Tables

The phase columns below are original planning slots retained for context,
not current target-release or delivery status. Read current priorities and
targets from the registries above; dormant React and extension projections
use the future planning phase. These tables neither reopen completed work
nor require the finite candidate to implement the wider historical milestones.

### Original High-ROI Slots

These slots explain the original value-to-effort prioritization before large
new subsystems; they are not the current development queue.

| Capability                                   | Phase | Priority | Why It Is Early                                                                                                                   |
| -------------------------------------------- | ----- | -------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Target repository proof bundle               | 1.1.0 | P1       | Aggregates existing target-repo evidence into one honest proof envelope before adding broader product claims.                     |
| Scoped SAGE/target-repository projections    | 1.1.0 | P1       | Reuses surgical governance in both directions without mixing release proof, customer findings, debt, lessons or authority scopes. |
| Deterministic source-release agent bootstrap | 1.1.0 | P1       | Makes the current source-checkout distribution verifiable and role-aware without falsely claiming wheel/PyPI support.             |
| Documentation and knowledge governance       | 1.1.0 | P0       | Keeps public claims, docs and code evidence aligned.                                                                              |
| Local governance telemetry                   | 1.1.0 | P1       | Turns correction loops and proof debt into local-first measurable evidence.                                                       |
| External evidence adapter fabric             | 1.4.0 | P0       | Normalizes specialist CI, security, test and observability evidence into Atlas without rebuilding those tools.                    |
| Standard Python package distribution         | 1.4.0 | P1       | Adds wheel/PyPI plus pipx/uvx delivery only after package-data, storage and upgrade boundaries are proven.                        |

### Original Platform Hardening Slots

These improve enterprise usefulness and daily operational quality, but should
follow stable v1 contracts.

| Capability                           | Phase | Priority | Scope                                                                                                                                                              |
| ------------------------------------ | ----- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Data privacy and leakage cage        | 1.2.0 | P1       | DTO/mapper and sensitive-object leakage analysis.                                                                                                                  |
| Performance and N+1 cage             | 1.2.0 | P1       | Policy-backed MVP exists for loop-contained async I/O candidates; next work is corpus calibration and precision hardening.                                         |
| Resilience and error handling cage   | 1.2.0 | P1       | Policy-backed MVP exists for naked external call candidates; next work is framework/client calibration and precision hardening.                                    |
| Cognitive load cage                  | 1.2.0 | P1       | Roadmap slot for complex conditional, nested flow, shallow abstraction, over-layering and premature boundary analysis. V1 only uses advisory principle-pack hints. |
| Engineering principle pack expansion | 1.2.0 | P1       | Expands advisory engineering wisdom without turning doctrine into a giant rule book.                                                                               |
| AST daemon service                   | 1.4.0 | P1       | Long-lived AST service for lower repeated parse/startup cost.                                                                                                      |
| IDE and LSP surface                  | 1.4.0 | P1       | IDE-native diagnostics from trusted SAGE artifacts.                                                                                                                |
| Shadow variation lab                 | 1.4.0 | P1       | Sparse sandbox variations and merge certificates.                                                                                                                  |
| Bounded agent execution sandbox      | 1.4.0 | P1       | Adds resource, filesystem, command and approval boundaries around agent execution after the threat model is stable.                                                |
| Third-party plugin runtime           | 1.5.0 | P1       | Loads external executable extensions only after sandbox, trust, artifact and claim contracts are mature.                                                           |

### Original React Topology Slots

These deepen the React ecosystem claim without turning v1 into a runtime product.

| Capability                   | Phase | Priority | Scope                                                             |
| ---------------------------- | ----- | -------- | ----------------------------------------------------------------- |
| Provider topology engine     | 1.3.0 | P1       | Provider tree and consumer reachability maps.                     |
| Auth and permission graph    | 1.3.0 | P1       | Auth boundaries, RBAC checks and protected route flow.            |
| Cache and revalidation graph | 1.3.0 | P1       | Cache key ownership, invalidation and revalidation relationships. |
| Route data to mutation graph | 1.3.0 | P1       | Route loaders/actions/server actions to mutation impact.          |
| State topology engine        | 1.3.0 | P1       | Stores, atoms, machines, selectors and mutation surfaces.         |
| Server state graph           | 1.3.0 | P1       | TanStack Query, SWR, Apollo, Relay and tRPC state surfaces.       |
| Bundle intelligence          | 1.3.0 | P1       | Heavy import, bundle boundary and build-output correlation.       |

### Strategic Future Intent

These are important, but they should not block v1 or near-term hardening.

| Capability                       | Phase | Priority | Scope                                                                                                                     |
| -------------------------------- | ----- | -------- | ------------------------------------------------------------------------------------------------------------------------- |
| Tree-sitter polyglot driver      | 2.0.0 | P2       | Deeper non-React AST drivers and normalized symbol schemas.                                                               |
| Runtime trace bridge             | 2.5.0 | P2       | Browser/dev runtime traces and render/event/state propagation proof.                                                      |
| Historical cognition memory      | 2.5.0 | P2       | Drift timelines, recurring failures and long-lived repo memory.                                                           |
| Distributed service governance   | 2.5.0 | P2       | NetworkEdge/ContractEdge graphs, API contract DNA and cross-repo blast radius for service-oriented systems.               |
| Bounded multi-agent coordination | 3.0.0 | P2       | SAGE is already multi-agent compatible; future scope is zone ownership, locks, conflict prevention and handoff contracts. |
| Private eval and learning loop   | 3.0.0 | P2       | Organization-specific private evals from release proof, outcomes and human review.                                        |

## Target Composition

`Repository Digital Twin` and `Sovereign Engineering Kernel` are target compositions, not standalone current capabilities. They emerge only when Atlas, Doctrine, ContextOS, external evidence, historical cognition and runtime evidence share provenance-preserving contracts. They must not be marketed as completed v1 features.

## Non-Goals

These are explicitly not treated as done:

- claiming universal React perfection without runtime proof
- claiming deep polyglot nanometric analysis outside the proven React/TypeScript vertical
- claiming every roadmap capability is active
- treating future capability slots as release claims

The goal is disciplined, explainable, high-trust evolution rather than inflated
completion language.
