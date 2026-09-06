# External Target Runbook

Nexora SAGE is developed and stored centrally, but it can analyze another repository or folder without being copied into that repository.

This is the preferred product mode for AI-assisted IDEs:

```text
SAGE local install
  -> MCP server
  -> target_root repository
  -> isolated external target artifacts
  -> Markdown/YAML agent briefs
```

The AI coding agent should edit the target repository. It does not need to read SAGE source files.

## Canonical Execution Identity

SAGE execution identity has two independent axes:

| Subject scope        | Acquisition mode    | Meaning                                                                                         |
| -------------------- | ------------------- | ----------------------------------------------------------------------------------------------- |
| `SAGE_ON_REPOSITORY` | `DEFAULT_WORKSPACE` | Analyze the explicitly configured default repository and project scope.                         |
| `SAGE_ON_REPOSITORY` | `EXPLICIT_TARGET`   | Analyze an explicitly supplied repository in an isolated namespace.                             |
| `SAGE_ON_SAGE`       | `DEFAULT_WORKSPACE` | Private maintainers evaluate SAGE product governance, release, lessons, debt and proof.         |
| `SAGE_ON_SAGE`       | `EXPLICIT_TARGET`   | Private maintainers use an explicitly authorized `sage_self` profile with isolated acquisition. |

`SAGE on default repo` and `SAGE on external repo` are not separate repository
semantics. They are two acquisition modes of `SAGE_ON_REPOSITORY`. They MUST
use the same canonical topology producer, engine semantics, applicability rules
and claim boundaries.

Acquisition mode may change only repository-root selection, artifact and SQLite
namespace, cache/freshness lifecycle, permission routing and acquisition
provenance. For the same root, snapshot, config projection, project scope and
pipeline profile, default and explicit-target results must be semantically
equivalent. Differences are parity findings, not an alternative ontology.

Every scoped runtime artefact is resolved from the declarative artifact
registry and projected beneath the active target `.raw` storage root. An
explicit-target watchdog MUST NOT read from or write to the default workspace
namespace. Scoped Audit and graph evidence bind both the target descriptor and
the committed Atlas snapshot identity; a missing, cross-target or stale
generation identity is invalid evidence and MUST NOT fall back to another
namespace.

The same target boundary applies to executable follow-up guidance. Watchdog
proof-debt actions are derived from declared refresh modes as structured argv.
For explicit-target acquisition every emitted daily, full, or release-deep
action includes the exact `--target-root <subject_root>` binding. If that
identity is unavailable, SAGE blocks command emission instead of presenting a
target-agnostic command. Copying a recommended command must therefore preserve
the repository that produced the debt.

Current development-source behavior: MCP call timing and bounded fallback
diagnostics use the product-global `output/.operational/mcp/` namespace. They do
not write into default or explicit-target evidence databases, retain target
paths or response bodies, or authorize evidence fallback. The raw state-payload
and payload-fingerprint readers used by this boundary close their SQLite
connections explicitly; the dedicated subprocess authority validator compares
default, target-A and target-B manifests and fails if target namespaces or
SQLite sidecars remain changed after process exit. This is a v1.1 roadmap
delivery and does not retroactively expand the published v1.0.3 claim.

The public distribution exposes only `SAGE_ON_REPOSITORY`. It may analyze the
SAGE source tree as an ordinary target repository, but that does not grant
release, lesson, debt, roadmap, seal or publication authority. Folder names,
source markers, target-root equality and repository identity are observations;
none may auto-escalate an actor to `SAGE_ON_SAGE`.

`SAGE_ON_SAGE` is a private maintainer subject. It requires both an explicit
`sage_operator_debug` actor/tool profile and an explicit `sage_self` reality
target profile. Acquisition can be default-workspace or explicit-target, but
neither acquisition mode supplies that authority by itself.

Private SAGE lesson, debt, work-item, execution-wave and roadmap registries are
not part of the public distribution. Reusable target-bound lesson projection
and action-memory contracts remain in the source as bounded components; they
consume target-scoped evidence and do not expose or inherit SAGE's private
institutional memory. Version 1.0.0 does not claim a general-purpose learning
product or a public self-governance surface.

## Configured Default Mode

After an explicit `init --target-root <repository>`, an embedded/default
installation may analyze that compiled workspace without repeating the target
argument. Current default root comes from:

- `config/codemaps.config.json`
- `workspace_root`

## External Target Mode

Use `--target-root` to analyze another folder without copying SAGE into that folder and without rewriting config:

```powershell
python .\sage.py run --full --target-root "<absolute-path-to-target-repo>"
```

SAGE runs a preflight automatically before the external target analysis. To skip only that preflight:

```powershell
python .\sage.py run --full --target-root "<absolute-path-to-target-repo>" --skip-target-preflight
```

Targeted step example:

```powershell
python .\sage.py run --step react-support --target-root "<absolute-path-to-target-repo>"
```

After artifacts exist, focused inspection belongs to the public MCP profiles.
Use `inspect_file`, `inspect_folder` or `inspect_symbol` with an explicit
`target_root`. The legacy targetless inspection CLI is private until it has the
same target-root and provenance contract; do not redirect it to internal raw
artifact paths as a workaround.

Watch-once example:

```powershell
python .\sage.py watch --once --target-root "<absolute-path-to-target-repo>"
```

Add `--path "<exact-source-file>"` for a one-file surgical pulse. A directory
path is a deterministic smoke sample and reports omitted candidates; it is not
a freshness claim for every file in that directory. `run --scope` remains a
directory-only Scoped Host Analyzer report filter and does not bound Atlas or
pipeline execution.

## MCP Product Flow

When SAGE is exposed as a local MCP server to Cursor or another MCP-capable IDE, use this target-repository flow:

```text
1. external_target_preflight(target_root)
2. If current target artifacts do not exist, run the bounded public CLI flow:
   python sage.py run --target-root <target_root> --profile daily --projects MAIN
3. get_surgical_operation_packet(target_root=target_root)
4. inspect_file(file_path, target_root=target_root)
   - If a source snippet is marked `partial_included_span_too_large`, call the suggested `inspect_file(file_path=..., line_start=..., line_end=..., target_root=target_root)` before editing omitted lines.
   - `source_grounded_for_inspection=true` proves current source identity, not edit authority. Mutate from this response only when `one_shot_edit_ready=true` and `inspection_authority=bounded_edit_context`; partial, omitted or ordinary file-level excerpts remain `orientation_only` until the required source range is inspected.
5. inspect_symbol(symbol, target_root=target_root, project="MAIN")
6. get_impact_radius(target_node, target_root=target_root, depth=2)
7. get_test_impact(target_file, target_root=target_root)
8. get_confidence_score(target_file, target_root=target_root)
9. trace_upstream_cause(target_node, target_root=target_root)
10. get_active_signals(target_root=target_root)
11. For cooperating agents editing the same file, switch to the
    `target_repository_followup` MCP profile and acquire
    `manage_target_write_lease(action="acquire", target_file=..., actor_id=..., target_root=target_root)`.
12. Return to `target_repository_default`, then call
    `validate_patch(target_file, patch_content, target_root=target_root, actor_id=...)`.
13. After applying the approved patch externally, use the follow-up profile to
    release `manage_target_write_lease(action="release", target_file=..., actor_id=..., target_root=target_root)` immediately.
14. get_dead_code(path, target_root=target_root)
15. get_state_flow(target_root=target_root)
16. get_ui_architecture(component, target_root=target_root)
```

`run_external_target_analysis` is a declared heavy server capability, not a
tool exposed by the compact public MCP profiles. Public agents dispatch the
repository analysis through the visible `sage.py` CLI, then consume its
target-bound artifacts through MCP. Guessing the hidden tool name must fail
closed rather than widen the active profile.

Use `refresh=true` when source grounding reports stale, missing or drifted
evidence and cache reuse must be bypassed without escalating to release-deep
claim semantics. A cached full run is not freshness proof. In private
maintainer operation, the explicit `sage_self` profile requires refresh after
SAGE source changes.

Fail-closed agent queries return consumer-specific recovery when available.
For example, `get_violation_work_queue` consumes Atlas and Audit evidence, so
its structured `recovery_plan.command_argv` requests a project-bounded
`--step Audit` run rather than a global Quality Gates refresh. The plan names
its authority chain, reports the currently required Nuclear Sequencing
dependency, and leaves duration unavailable when no current run-receipt
estimate exists. Actors must not widen or retry that operation merely because
their transport timed out.

The surgical packet has an additional required-input generation gate. A legacy
generation may fail the broader artifact-trust summary or pass that summary
while its `signals` receipt is bound to an older Atlas snapshot. In either
state, `get_surgical_operation_packet` returns `INVALID_CONTEXT` with the same
executable `daily --projects MAIN --refresh` recovery plan. It does not first
prescribe Quality Gates and then require a second command. The plan identifies
the daily profile and requested project closure; measured duration remains
unavailable until the terminal-receipt contract exists. `already_current`, a
blocked required input, and an empty command are not a valid combination.

Defaults are agent-facing:

- `get_surgical_operation_packet(..., format="brief")` returns Markdown with YAML.
  Its visible L1 dependency projection is refreshed from the same snapshot-bound
  SQLite dependency helper used by `get_impact_radius`; total, shown and omitted
  counts plus graph source and snapshot identity are explicit. An empty shown
  list is not an absence claim when omitted is non-zero or the SQLite projection
  is unavailable.
- `inspect_file(..., format="brief")` returns Markdown with YAML. Optional `line_start`/`line_end` returns a narrower source-grounded snippet from the same target snapshot.
- `inspect_folder(..., format="brief")` returns Markdown with YAML.
- `inspect_symbol(..., format="brief")` returns Markdown with YAML. It defaults to `project="MAIN"` so a coding agent does not treat variations as edit targets. Use `project="all"` or a scoped `PROJECT::Symbol` only for explicit merge/variation review.
- `get_impact_radius(..., format="brief", depth=2)` returns Markdown with YAML, shown/omitted dependent counts, duration, and follow-up commands. Use `depth=3` only when direct evidence cannot explain the failure or the human asks for broader impact; use `depth=0` only for full-graph/debug review. It fails closed if dependency graph evidence is missing for `target_root`.
- `get_test_impact(..., format="brief")` returns Markdown with YAML from the
  selected default or explicit-target artifacts. Both acquisition modes
  supplement graph candidates through the same bounded live sibling scan for
  co-located tests that directly import the target, including newly created
  untracked tests. The result is a prioritized candidate set, not proof of
  complete test coverage.
- `get_confidence_score(..., format="brief")` returns Markdown with YAML from isolated target artifacts.
- `trace_upstream_cause(..., format="brief")` returns Markdown with YAML and fails closed if upstream graph evidence is missing for `target_root`.
- `get_active_signals(..., target_root=...)` reads isolated target signals and fails closed when the target has no signal artifact.
- `validate_patch(..., target_root=..., format="brief")` keeps four verdicts
  separate: SAGE governance, source/snapshot grounding, exact non-mutating Git
  patch applicability, and target-repository-native policy/test validation.
  `governance_validation_passed` does not authorize application. The current
  V1 surface runs `git apply --check` for a grounded unified diff, but reports
  target-native policy validation as `NOT_RUN`; therefore `safe_to_apply`
  remains false until the repository's own lint, compiler, policy and focused
  test oracles have been run.
- MCP `patch_content` is a JSON string, not a shell object. Native MCP clients
  should send the file contents directly. A PowerShell bridge must force a
  scalar string, for example
  `[string][System.IO.File]::ReadAllText((Resolve-Path '.\change.patch'))`;
  piping `Get-Content -Raw` through a generic object serializer can produce a
  PSObject-shaped value that the MCP schema correctly rejects.
- Do not place a large patch into a process command-line JSON/base64 argument on
  Windows. That bridge can exceed the operating-system argument limit before
  SAGE receives any bytes; it is a client transport failure, not a
  `validate_patch` result. The V1 contract supports normal native MCP string
  transport but does not claim a public file/stdin/resource upload path for
  large payloads. Byte-safe large-patch transport is tracked for 1.1.
- A target write lease coordinates cooperating agents that share this SAGE target-analysis SQLite database. It does not lock a repository across disconnected SAGE installations; refresh source grounding after every external write.
- Supporting-context tools such as `check_module_integrity`, `simulate_change_impact`, `get_surgical_context`, `get_dead_code`, `find_clones`, `get_state_flow`, `get_circular_dependencies`, `get_blast_radius`, `get_health_metrics`, `get_hexagonal_bindings`, and `get_ui_architecture` also accept `target_root` and default to bounded Markdown/YAML briefs. If the target-specific artifact has not been generated, they return `# Target Evidence Missing` instead of reading SAGE's own workspace artifacts.
- Impact/upstream tools may use `dependency_graph_source: "atlas_imports_fallback"` when an external target has `atlas.json` but no `circular_deps.json`. Treat that as bounded static import orientation for editor navigation; do not treat it as cycle classification or full runtime causality proof.
- A project-filtered run may preserve other project payloads in the canonical
  Atlas while excluding them from execution. Read `meta.execution_scope` on
  Dead Code, Circular Dependencies and React Frontier artifacts:
  `requested_projects`, `analyzed_projects`, `preserved_only_projects` and
  `unavailable_requested_projects` are different authorities. Do not attribute
  preserved-only findings or metrics to the requested target.
- Scoped Atlas persistence reports a structured fallback diagnostic when the
  relational project set or an unscoped project file set has drifted. A
  `full_fallback` remains correctness-preserving but is a performance debt, not
  evidence that the bounded request was cheap.
- `format="json"` is reserved for machine contracts.
- `format="brief_debug"` is reserved for troubleshooting SAGE graph/provenance internals.
- Debug/provenance and heavy-validation tools are opt-in. They are useful for SAGE audits, false-positive investigations, release claims, or setup work, but they should not be preloaded into ordinary target-repository coding context. Use `config/mcp_tool_roles.json` as the role source of truth.

The default brief must describe target-repository files and actions, not SAGE internals. Internal graph identifiers belong only in debug output.
When a required artifact is missing for the selected external target, SAGE should say that explicitly instead of answering from the central SAGE workspace artifacts.

## Agent Path And Safety Rules

- Open files with `analysis_root + target_file`, `analysis_root + related_files`
  or `analysis_root + inspect_first`. Do not open `target_ref` as a filesystem
  path.
- Use `target_ref` only for follow-up SAGE/MCP calls when a project-scoped
  reference is useful.
- Search results are not patch directives. Inspect the returned candidate first.
- `get_confidence_score` is a risk estimate, not merge/deploy approval.
- If `validate_patch` reports `mcp_target_not_grounded`, a missing target or an
  unindexed target, refresh target evidence or request an explicit create-file
  workflow before editing.
- If `validate_patch` reports `exact_patch_not_applicable`, regenerate the
  unified diff from the current source bytes. A semantically valid change is
  not an applicable patch.
- Call `validate_patch` before applying the proposed bytes. A post-apply
  no-effect or source-mismatch result is truthful but cannot retroactively
  authorize the mutation; compare current content with prior grounded evidence
  and run target-native validation instead. Already-current full-file content
  terminates as `NO_OP`/`NO_CHANGE`; transport shape, lease and seal facts remain
  non-actionable because no target bytes would change.
- A SAGE remediation recommendation is not proof that the target repository
  permits the proposed dependency or import. Until target-native policy
  ingestion is active, use the repository's effective lint/compiler policy as
  the remediation-legality oracle and prefer bounded strategy classes over an
  unverified concrete import.
- A clean SAGE Audit is not proof of async mutation or persistence correctness.
  For Promise-returning mutation APIs, verify that the authoritative side effect
  is awaited or returned and that nested failures propagate. Logging alone is
  not error propagation; intentional detached work should be explicit and own
  its failure lifecycle.
- The technical-debt work queue should expose actionable work items when
  evidence is ready. If evidence is not ready, it must fail closed and tell the
  agent to refresh SAGE evidence before editing.

## Runtime Contract

External target mode sets `CODEMAPS_TARGET_ROOT` only for the subprocess run.

The active runtime config becomes:

- `workspace_root`: the provided target folder
- `source_mode`: `external_target`
- `variations`: the projects selected by canonical repository topology discovery
- `project_roles`: canonical `host`, `variant`, `companion`, or `unresolved`
  relationship roles
- `analysis_projection`: `single_project` or `multi_project`, derived from discovered topology

External acquisition does not define a second repository ontology. Normal
workspace discovery and external target discovery use the same canonical
candidate and role producer. The external path adds target-root acquisition,
runtime isolation, and an isolated output namespace; it does not flatten nested
projects into `MAIN`.

`MAIN` remains the safely inferred host project scope, such as `src` for a
root application. A nested candidate with its own recognized config/manifest,
a declared workspace edge, a policy-recognized relation container, or strong
independent structure is promoted to a separate project key for repository
coverage. Coverage selection does not invent its relationship role: a candidate
without relationship evidence remains `unresolved`. Every discovered project
boundary owns its subtree even when an explicit caller filter bounds it out.
Parent Atlas walks therefore exclude all nested discovered project roots: a
bounded-out embedded project cannot leak back into `MAIN`, and a selected child
cannot be counted under multiple project keys.

Architecture-shaped source containers remain different from project boundaries.
When a manifest-bearing parent owns a conventional architecture directory such
as `src` or `app`, the directory's internal `components`, `pages`, or
feature structure remains parent-owned orientation evidence. It becomes a
nested project boundary only when it carries its own config/manifest, matches a
declared workspace edge, or is otherwise discovered as an independent
structural root rather than merely a named source container.

Preflight exposes:

- `source_mode`
- `ontology_contract`
- `discovered_topology`
- `analysis_projection`
- `project_candidates` and `project_candidate_roles`
- `project_candidate_role_authority` and `project_candidate_system_kinds`
- `project_candidate_selection_evidence`
- `selected_projects` / `auto_selected_projects`: topology selection before a
  caller runtime filter
- `coverage_only_projects`: selected project boundaries whose source is included
  in analysis but whose host/variant/companion relationship remains unresolved
- `relationship_operation_projects`: selected projects eligible for
  relationship-dependent comparison or merge behavior
- `requested_project_filter`: the caller's explicit `--projects` request
- `effective_runtime_projects`: the projects authorized for this execution
- `unavailable_requested_projects`: requested keys absent from discovered
  topology
- `excluded_projects`
- `project_ownership_exclusions`
- `comparative_analysis_enabled`

Discovery and execution are separate authorities. A project may be visible in
`project_candidates` or `auto_selected_projects` without belonging to the
current run. Claims about analyzed scope must use
`effective_runtime_projects`, not the broader topology inventory.
Preflight language and framework inventory remains conservatively topology-wide
and is labelled with `inventory_project_scope`; it may warn about a discovered
project outside the requested runtime filter but cannot expand the runtime
claim. A requested project absent from discovered topology fails closed.

After Atlas is materialized, the orchestrator writes one immutable
`POST_ATLAS` `analysis_scope_authority` artifact. Audit, Architecture Oracle,
Validation Oracle, and Quality Gate consume that exact authority rather than
deriving a competing repository scope. Their scope identities are reconciled in
the terminal run receipt, but terminal reconciliation does not rewrite the
post-Atlas authority or its lineage receipt.

Agent-facing action surfaces additionally require the SQLite-primary content
digests for the scope authority, Audit, and Quality Gate to match COMPLETE
lineage receipts bound to the current Atlas snapshot. Matching project names,
timestamps, or embedded authority IDs alone are not sufficient action evidence.

The authority distinguishes three outcomes:

- `COMPLETE_REPOSITORY`: all claim-eligible supported source discovered for the
  repository is represented by the effective runtime scope.
- `BOUNDED_PROJECT_SELECTION`: an explicit caller selection such as
  `--projects MAIN` is complete for that declared project boundary, but is not a
  whole-repository claim.
- `INCOMPLETE_EVIDENCE`: automatic omission, truncation, unavailable requested
  projects, or consumer disagreement prevents an actionable repository claim.

A zero process exit reports execution completion. It does not upgrade bounded
or incomplete scope into complete-repository evidence.

Automatic repository coverage includes the host scope plus evidence-bearing
nested candidates discovered from declared workspace matches, policy-recognized
containers, candidate-local config/manifests, or strong independent structure.
Config, manifest, or structural evidence proves a boundary and grants analysis
coverage; it does not by itself prove that the candidate belongs to the host's
comparative topology. A candidate without relationship evidence remains
`unresolved`, is exposed under `coverage_only_projects`, and is denied as a
host-merge source. This prevents both silent omission and fabricated companion
or variant semantics. It also avoids file-count promotion: source volume alone
does not establish a repository relationship.
A single-project projection is a bounded selection, not evidence that no nested
project exists. Semantic depth still follows the language/framework capability
matrix; multi-project discovery does not grant unsupported-language authority.

`host`, `variant`, and `companion` are governance relationship roles. They do
not claim that a project is an application, library, tool, or microservice.
Technical system-kind inference requires a separate evidence-bearing
classification stage. V1 keeps that inference conservative; a future two-pass
Topology Oracle may consume candidate-local fingerprints or bounded scout
Atlases before full Atlas materialization. Until then an otherwise unproven
candidate remains `unresolved` and coverage-only; a
policy-recognized container role remains a
medium-confidence inference rather than a resolved relationship, and its
technical system kind remains `unknown`.

The compiled SAGE config files are not modified.

External target outputs are isolated under:

```text
output/external_targets/<target-name-and-hash>/
```

This keeps quality-control runs from overwriting the default analysis artifacts.

`output/external_targets/` is runtime evidence, not a durable archive. Before
cleaning runtime artifacts or packaging a release, promote any external-corpus
result that matters to a current evidence document or a versioned corpus
manifest. A release claim should cite the promoted summary/manifest, not only a
volatile `output/` path.

Generate the external target run index:

```powershell
python .\sage.py external-targets
```

Clean only external target outputs:

```powershell
python .\sage.py purge --mode external-targets
```

## Boundary

External target mode is for repository-independent analysis, target-repository AI coding, and quality-control runs.

Do not copy SAGE into target repositories. Keep SAGE central and pass the target path as input.

The target repository should receive:

- file paths,
- concrete findings,
- smallest-safe-change instructions,
- validation suggestions,
- HITL boundaries when needed.

The target repository agent should not need:

- SAGE source paths,
- raw artifact paths,
- architecture profile nicknames,
- internal graph node identifiers unless `brief_debug` is explicitly requested.
