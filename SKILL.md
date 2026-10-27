# Nexora SAGE Skill

> Role: AI-native codebase intelligence with Human-in-the-Loop governance
> Interface: `tools/mcp/server.py` (via `python sage.py mcp`)

Start this target-repository surface with
`python sage.py mcp --profile target_repository_default`. The MCP boundary
derives the visible tool set from `config/agent_surface_contract.json` and
`config/mcp_tool_roles.json`; SAGE-internal tools are not merely discouraged,
they are absent from this profile. Start a separate
`target_repository_followup` profile only when a concrete target or HITL need
exists.

This skill describes how an AI agent should use Nexora SAGE in any repository.
It is intentionally repository-agnostic. Project names discovered at runtime
come from `codemaps.discovery.json` and `codemaps.config.json`, not from this
document.

This is the `SAGE_ON_REPOSITORY` projection. It excludes SAGE product-development
lessons, debt, roadmap, release proof and internal manual-audit state. Relevant
capability context is filtered to `SAGE_ON_REPOSITORY` and `SHARED_PLATFORM` by
the canonical scope taxonomy.

All target-repository actors must preserve the channel-independent boundary
`docs/SAGE_ACTOR_INTERACTION_CONTRACT.md` projection. Its machine-readable owner
is `config/actor_interaction_contract.json`; this skill narrows that contract to
the target-repository role and does not redefine authority or validation semantics.

## Core Rule

- Nexora assists the AI agent; the AI agent assists the human; the human owns approval.
- Treat `codemaps.config.json` as runtime truth.
- Treat `codemaps.discovery.json` as machine proposal.
- Treat `codemaps.overrides.json` as human-governed policy.
- Prefer Atlas / Genome / Closure artifacts over manual guessing.
- Read the surgical operation packet or active signals before opening very large
  artifacts. Read the published evidence documents for SAGE product readiness;
  private self-release and debug/provenance tools are not part of this profile.
- Treat missing or empty ContextOS active signals as an idle/fail-closed state,
  not as proof that the analyzed repository has no risk.
- ContextOS path metadata comes from a verified read of the selected target's
  current Atlas, shared only within the request. `unavailable_primary` or
  `unresolved` path metadata means a displayed path is orientation, not verified
  editor navigation; refresh the target evidence before using it for a change.
- Capability activation context is a read-only view of the selected target's
  artifacts. Missing or invalid plans are explicitly unavailable; plan presence
  alone does not prove snapshot freshness, enable rules or authorize mutation.
  Do not substitute installation-wide context or private development planning.
- Treat active signals as current-turn scope only when the response declares
  `current_change_scope=bounded` and `current_turn_claim=supported`. Broad or
  persisted signals with unknown scope are orientation; confirm the current
  changed-file set before task-local use.
- Keep default target-repository brief instructions in English. Preserve exact
  repository paths, symbol names, evidence snippets, and user-authored strings
  even when they contain another language.

## First Call Order

Default target-repository coding turns should stay narrow. Do not preload
supporting or heavy-validation tools before a concrete target-repository need
exists. Product self-release and maintainer-debug tools are unavailable in the
public profiles even when an agent guesses their names.

1. `get_surgical_operation_packet(max_signals?, format?, target_root?)` for the default target-repository surgical brief. Use `target_root` to read an isolated external-target analysis. Use `format=json` only when a machine-readable SAGE contract is explicitly needed; use `format=brief_debug` only for troubleshooting internal graph references.
2. `get_violation_work_queue(page?, page_size?, rule?, project?, severity?, target_root?)` when the user wants to pay down existing technical debt after the first repository analysis. A zero-row result is clean only within SAGE Audit; target-native enforcement remains separately required.
3. `get_active_signals(scope?, include_bodies?, target_root?)` when you need the latest persisted surgical orientation or a producer-declared bounded current change scope.
4. `search_symbols`, `inspect_file`, `inspect_folder`, or `inspect_symbol` for targeted work.
5. Use `supporting_context` tools only after a concrete file, symbol, rule, or issue exists.

Activation, contract and architecture context are read-only orientation. When
`full_plan_followup.availability=unavailable` or `followup.availability=unavailable`,
no target-profile full-plan, full-contract or private Oracle follow-up is offered.
Architecture source-artifact references remain provenance, not a fallback access
route. Unavailable follow-up does not mean the supplied architecture evidence is
missing. Do not switch to a maintainer/debug profile or use another target's plan,
contract or Oracle as a fallback. This does not prove readiness or grant
activation, scheduling or edit authority.

Missing target artifacts and missing/stale watchdog sessions do not authorize
heavy analysis. Follow the scoped `recovery` guidance: an unavailable MCP refresh
requires an authorized operator, not guessed hidden calls or profile switching.
A `cli_manual_alternative` is an argument list to review, not an executed command
or permission; replace its explicit-path placeholder only after scope/authority
is established. Preserve its target root, installation working directory and
primary-versus-external namespace. A directory pulse is smoke sampling, not
captured changes or repository-wide proof; stale findings remain historical
until an actual scoped refresh succeeds.

## Target Repository Path Contract

Primary inspection, impact and Test Impact recommendations from a follow-up
packet use its `primary_tool_followup` projection. Use the current connection
when the tool is visible, or a separate configured primary connection for the
same target; reconnect only if that connection is absent. Preserve the exact
`target_root` argument, including an empty primary root. Bind each central
`call_template` to one intended target: templates are not complete or executed
calls. For example, `inspect_file` takes `file_path`, not `target`. Visibility
does not grant edit, lease, refresh or runtime-proof authority; unavailable and
SAGE-self-only context must not be routed through a target-profile bypass.

- Open files with `analysis_root + target_file`, `analysis_root + related_files`
  or `analysis_root + inspect_first` entries.
- Treat `target_ref` values such as `MAIN::src/App.tsx` as SAGE/MCP follow-up
  references only. They are not filesystem paths.
- `target_ref` format is `<project>::<repo_relative_path>`. `MAIN` means the
  primary analyzed project/workspace scope; variation or external project
  prefixes are explicit scopes, not folders to open directly.
- Search results are candidates, not patch instructions. Use the returned
  `next_tool` or call `inspect_file` / `inspect_symbol` before editing.
- Search counts describe indexed declaration, file and derived-syntax candidates,
  not distinct files or whole-source-tree coverage. `candidate_count` is a lower bound when
  `search_truncated` is true. `shown`, `omitted` and `display_truncated` describe
  only the collected list's display; further source-cap omissions are unknown.
  Narrow project/path/query to inspect omitted candidates. Machine output keeps
  the existing list shape and repeats count metadata on each returned row.
- Indexed symbol/file SQLite and Atlas fallback searches use Python lowercase
  matching for Unicode names and paths. Recorded derived-syntax SQLite readers
  use the same semantics for indexed path and project scope. This is not full
  Unicode case folding, filesystem normalization or a guarantee that the
  Atlas fallback projects derived-syntax candidates.
- ClassMethod search rows preserve the declaring class and recorded member span,
  not proven calls or runtime behavior. Use their inspect_file follow-up.
  Check class_method_search coverage: legacy/unprojected or malformed evidence
  is unavailable/partial, not proof of absence. This scope excludes constructors,
  interface/computed/quoted members and initializer/store actions.
- StoreActionCandidate rows separately expose direct setter-call member syntax in
  checker-bound named-import or namespace-qualified Zustand create/createStore
  initializers, preserving the declaring
  variable and recorded span. Check store_action_search coverage, then inspect
  the file before editing. They do not prove call-result ownership, execution,
  live source freshness or complete store-action coverage; unknown factories,
  arbitrary objects, function members without recorded setter calls, import
  bindings and other qualified calls remain outside scope.
- ImportBindingCandidate rows expose recorded top-level ES import aliases, imported
  names, type-only syntax and source spans. Check import_binding_search coverage
  and inspect_file before acting. They do not prove use, resolution or execution;
  require, dynamic imports, reexports and qualified calls remain outside scope.
- For a structured `propose` request, call `validate_actor_proposal` through
  `dispatch_actor_request`. `PROPOSAL_CONFORMANT` proves envelope identity,
  snapshot reference and declared-scope conformance only; it never authorizes
  execution or proves that the proposal is technically correct.
- `validate_patch` is a pre-write safety check. If it reports
  `mcp_target_not_grounded`, `target_exists: false` or `target_indexed: false`,
  do not create or edit that path from the packet; refresh SAGE evidence or ask
  for an explicit create-file workflow with human approval.
- For a coordinated multi-agent edit of one file, use the follow-up-only
  `manage_target_write_lease(action="acquire", target_file, actor_id, ...)`
  after source grounding is current. Pass the same `actor_id` to
  `validate_patch`, apply the approved patch externally, then call
  `manage_target_write_lease(action="release", ...)` immediately. Do not use
  a lease for read-only inspection, and do not treat it as a filesystem lock
  across disconnected SAGE installations.
- `get_confidence_score` is a risk estimate and never a standalone merge,
  deploy or broad-refactor approval.
- Source-bound TypeScript syntax observations cover only the checked files,
  not project-wide type safety. Missing, stale or incomplete diagnostic input
  is unavailable evidence, never a zero-error result. Use the target's native
  compiler to establish semantic type correctness.
- A compiler-library input match covers only the observed positive reads against
  SAGE's invocation-owned installation inventory. It is not Atlas lineage,
  loaded-code attestation, or native-project proof; it cannot enable a semantic
  score or override a blocked diagnostic receipt.
- Source snapshot status `content_mismatch`, `atlas_mismatch` or
  `identity_unavailable` is not grounded evidence. Refresh the affected Atlas
  scope before relying on snippets or proposing a mutation; matching file size
  or timestamps cannot repair missing content identity.
- For `.d.ts`, `.d.mts` and `.d.cts`, `inspect_file` accepts an exact
  `PROJECT::project_relative_declaration_path` and returns a separate
  `declaration_contract` view. Its `declaration_ref` is a query reference, not
  an ordinary repo-relative `target_ref` or editor path. Only stored finite
  top-level headers are shown, with UTF-8 byte spans, parser/policy provenance,
  independent project/file status and producer/display omissions. A selected
  empty file is not complete project/type coverage. The bounded reader uses the
  selected SQLite or JSON primary; JSON identity hashes that same raw read
  through EOF, including BOM/newline bytes. This bounds retained metadata, not
  total I/O or file immutability. Legacy, stale policy, corrupt or missing
  evidence stays unavailable, without backend/shadow fallback. Neither the
  stored Atlas identity nor captured parser-input hash checks live source or
  grants source snippets, edit readiness, resolved exports/types or runtime
  proof. Line-range requests are unsupported for this metadata-only view;
  ordinary implementation inspection and patch validation remain separate.
- The optional `manifest_type_links` section in declaration inspection records
  same-project stored top-level `types`/`typings` literal path correspondence.
  It preserves the manifest field/order and raw manifest/declaration identities;
  it does not select the effective package entry or resolve conditions/types.
  Legacy/malformed/stale link evidence stays independently unavailable while
  valid headers remain usable. Link producer/display omissions are separate
  from header omissions; an absent or empty bounded section proves no global
  absence. Conditional exports, version selectors and unsupported targets remain
  unresolved. Neither a link nor its stored identity grants live-source,
  editor-path, runtime, semantic type-safety or mutation authority.
- Ordinary TypeScript `inspect_file` may also show `manifest_type_entry_links`:
  stored top-level `types`/`typings` literals point to that exact already-indexed
  source file, without reclassifying it as a declaration. Raw manifest identity
  and the source's tagged Atlas text identity are different facts. This uses
  the same selected file metadata read, not native condition/type resolution
  or live manifest validation. Missing/old/malformed evidence and surgical or
  partial auxiliary scope stay unavailable; retained positive links may coexist
  with partial manifest coverage. Producer and display omissions are separate; an empty or
  omitted section proves no absence. This late section shares remaining syntax
  records/tokens and the existing whole-packet budget, without evicting older
  source grounding or changing snapshot/edit/runtime authority.
- `get_test_impact` returns repo-relative test files and commands. Run listed
  tests from the target repository only when the command is clearly a
  target-repository package script; SAGE validation commands run from the SAGE
  workspace or through MCP.
- Test Impact confidence is a static candidate ranking, not behavioral coverage
  or a passing result. Read `candidate_evidence`: direct imports, transitive
  dependencies and naming-only matches remain recommendations. A legacy
  dual-vector match without its original graph relation stays unknown.
  `not_run_by_sage`, `not_established` and `not_assessed` do not certify
  execution of the edited symbol/effect or absence of mocks; inspect and run
  the target's focused native tests before making a behavioral claim.
- Default and external targets use the same static graph matcher with their
  own Atlas/dependency inputs. Missing external dependency artifacts retain
  naming/live-only candidates; SAGE does not borrow the host graph or silently
  derive another graph. An empty or failed result is not proof of irrelevance.
- Static impact follows tests that depend on the changed target, not tests
  that merely share its dependency. For `Provider -> Hook <- Hook.test`,
  the Hook test is not a Provider-impact candidate on that fork alone.
  A directed `Test -> Provider` or `Test -> Consumer -> Provider` path
  is different. Naming candidates remain independent recommendations;
  none of these relations certifies Provider integration or runtime behavior.
- Test candidates retain their own `project` and, when indexed, canonical
  `atlas_node`. Equal paths in different projects are distinct candidates;
  naming matches do not cross project boundaries. A legacy row without a
  consistent project identity cannot borrow the target project's source snapshot.
- Test Impact accepts the public `<project>::<repo_relative_path>` reference
  as well as the canonical Atlas node. When project roots differ from the repo
  root, SAGE resolves the public path through one indexed workspace record in
  that exact project before following graph edges. Exact Atlas keys take
  precedence; ambiguous, missing or other-project paths are not guessed.
- A configured absolute project root is a physical location, not a prefix for
  Test Impact files or commands. SQLite file contexts use that same artifact's
  declared `workspace_rel` for such roots; canonical project/node identity is
  preserved when a live sibling supplements an indexed test. Missing, escaping
  or ambiguous path metadata stays unresolved, not a guessed relative path.
- Search and SQLite impact-radius dependents use the same declared workspace
  path owner for absolute project roots. Search keeps an unresolved candidate's
  canonical `atlas_node` but exposes no openable path. Radius keeps graph counts,
  omits unresolved file paths and reports `unresolved_dependent_refs`; those refs
  are not filesystem paths. Path projection does not add graph edges or runtime proof.
- SQLite upstream traces use that same workspace owner. File context retains its
  canonical node and project-qualified reference through brief rendering and
  import-line attachment; a cross-project file cannot borrow the target project.
  Unresolved refs remain visible without openable paths or guessed snippets.
  Dependency counts describe bounded returned rows, not whole-graph totals;
  captured import lines are static evidence, not proof of runtime causality.
- Upstream import snippets join a captured file's `import_records.raw_source`
  and parser-recorded top-level binding span to the exact graph target. A
  resolved dependency key is not the source import's spelling. Missing,
  conflicting, type-only, unsupported or stale evidence stays omitted while
  the graph edge remains visible. Multiline snippets may show only the recorded
  binding, not the whole import statement; neither proves runtime execution.
- SQLite target source status retains the exact file context that established
  indexing, including its project and captured-source identity. Upstream and
  impact-radius consumers pass project-qualified references, not display paths.
  Snapshot availability alone does not authorize edits: live drift, missing or
  tampered content, ambiguous paths and confinement remain separate checks.
- Compact MCP source grounding co-selects a symbol span and its existing snippet
  by exact symbol/range before applying display caps. Useful paired coordinates
  precede unavailable metadata; duplicate coordinates are shown once. Full machine
  evidence is unchanged, and shown/omitted counts describe the compact selection.
  File-start orientation with no symbol spans stays explicitly unpaired. Partial,
  omitted or stale context never gains edit authority from this presentation.
- With JSON-only Atlas artifacts, target paths still belong to the exact indexed
  project/file. Explicit missing, malformed, escaping or ambiguous workspace
  metadata yields no openable target path in upstream/radius machine or brief
  output. Legacy metadata that omits `workspace_rel` can use its declared relative
  project root, but never a physical absolute root or an unknown file. Unqualified
  exact host keys keep their legacy priority; workspace aliases must be unique.
  Without SQLite, source snapshots and verified snippets remain unavailable:
  a live indexed file is not captured-source or edit authorization. JSON-only
  search and impact-radius dependents now reuse that same owner. Rejected paths
  keep canonical search candidates and unresolved graph refs, not guessed files.
  Search file names come from indexed keys, not display metadata. Radius graph
  counts, returned graph scope and traversal omissions retain unresolved nodes;
  omitted file paths are explained separately by `unresolved_dependent_refs`.
  This is a bounded path contract, not full JSON/SQLite search or graph parity.
- SQLite module-integrity findings use the same workspace path owner. Each row
  retains its canonical `atlas_node`; path status comes from that exact project,
  not an unqualified workspace alias. An unresolved path keeps the finding,
  rule and count, with empty `target_file`/`inspect_first` and explicit
  `path_projection_status: unresolved`. Do not infer an openable file from its
  canonical ref. This does not change Audit rules or generation/trust gates.
- Module-integrity selectors accept exact `<project>::<atlas_relative_path>`
  or `<project>::<repo_relative_path>` file references. Exact indexed keys win
  over workspace aliases; aliases resolve only inside that project. Missing,
  ambiguous, other-project or shorthand paths are not guessed. Unqualified
  input remains a literal path/module fragment, with the existing default MAIN
  scope. A zero result describes only the selected matches, not repository health.
- Violation queue rows retain their canonical `atlas_node` and finding identity.
  Only an exact SQLite workspace mapping with a present, confined, indexed file
  yields an openable `target_file` or `inspect_first`. An unresolved or rejected
  path retains the finding, count and approval decision, with empty file/inspect
  fields and `path_projection_status: unresolved`; resolve the canonical SAGE
  reference before editing, never guess a basename. Source snippets require
  verified captured bytes and matching live source, not a path suggestion.
- For a production change, query the changed production file rather than the
  hoped-for test filename. Compare `analysis_root`, target grounding and snapshot
  identity before diagnosing a missing candidate; another checkout's test is
  not part of this Atlas.
- When present, `mock_declaration_evidence` reports only snapshot-bound,
  module-root literal declarations with an observed framework import binding
  and an exact Atlas module resolution. It does not prove runtime substitution,
  execution order or absence of mocks. Missing/legacy/stale snapshots, global
  test-framework names, dynamic paths, setup files and evidence beyond the
  bounded source budget remain unknown. Use the listed test/source span to
  inspect the declaration and the target's native test runner to verify behavior.
- When a brief includes `validation_command_contracts` or
  `validation.command_contracts`, read them before running commands. Treat
  `focused_validator` as narrow evidence, `target_package_script` as target
  repository evidence, and broad/global/release-style commands as expensive
  proof envelopes rather than surgical context refreshes.

Optional target-repository support:

- Use broad handoff packets only when the user asks for a handoff. Keep ordinary
  coding turns on surgical/context tools.
- Use watchdog session context only when the current live-change session itself
  needs inspection.
- Response-contract tools shape or audit the agent answer; they do not provide
  target-repository evidence. Use them after evidence is gathered.
- SAGE-internal release proof, learning-loop, capability, pipeline, telemetry,
  provenance and developer/debug tools are intentionally not listed in this
  target-repository skill. Maintainer-only self-development guidance is a
  separate authority surface and is not part of the public target-repository
  package.

Every answer should separate evidence from inference and state whether human approval is required.

## MCP Tool Roles

SAGE MCP tools are classified in `config/mcp_tool_roles.json`.
The active server exposes only the selected machine-readable profile; a tool
outside that profile must fail closed even if an agent guesses its name.
The generated audit view is `output/.raw/mcp_surface_command_matrix.json`; it
maps every MCP command to its H1/H2 ring, family, audience, and review
obligations. If a tool is added or reclassified, run
`python -B .\tools\validate_mcp_surface_command_matrix.py`.

- `primary_agent`: default tools for coding inside the analyzed repository.
- `supporting_context`: call after a concrete file, symbol, or issue exists.
- `debug_provenance`: use for SAGE debugging, proof review, or false-positive investigation; do not feed these by default to a target-repository coding agent.
- `human_hitl`: read Progressive HITL approval posture and decision history.
- `mutating`: writes state or requests; call only with explicit intent and approval posture.
- `heavy_validation`: broad or expensive validation/setup; do not call as default context.

## MCP Tools

Tools classified as `supporting_context` are not part of
`target_repository_default`. After a concrete file, symbol, rule or issue
exists, switch to `target_repository_followup` before calling them. An
unavailable supporting tool is a profile-boundary signal; do not retry the same
call unchanged or infer that its underlying capability is absent.

| Tool                                                                                                                                       | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `get_operator_packet(refresh?, human_report?, projection?, target_root?, format?)`                                                         | Load a broad handoff packet. Default `projection="agent", format="brief"` returns the target-repository Markdown/YAML agent projection; `target_root` reads isolated external-target output; `format="json"` and full/operator/platform projections are SAGE-internal or machine review surfaces.                                                                                                                                                                                                                                                                              |
| `get_human_approval_gates()`                                                                                                               | Load current approval gates for risky AI actions.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `get_hitl_decision_requests(human_report?)`                                                                                                | Read structured AI-created requests for human review.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `create_hitl_decision_request(gate, scope, proposed_action, risk, rationale?, evidence?, confidence?, requested_by?)`                      | Create a request before risky AI actions.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `get_agent_response_template()`                                                                                                            | Read the required AI answer shape; this is answer governance, not target-repository evidence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `validate_agent_response(response_json)`                                                                                                   | Validate an AI answer against the response contract after evidence has been gathered.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `record_agent_response(response_json, task_id?, actor?)`                                                                                   | Validate and record a high-stakes AI answer for later audit.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `inspect_file(file_path, format?, target_root?, line_start?, line_end?)`                                                                   | Inspect one file from existing artifacts; `target_root` reads isolated external-target output and default `format=brief` returns a Markdown/YAML agent brief. Use `line_start`/`line_end` when a partial source snippet tells you to request a narrower source-grounded span before editing omitted lines.                                                                                                                                                                                                                                                                     |
| `inspect_folder(folder_path, format?, target_root?)`                                                                                       | Inspect one folder from existing artifacts; `target_root` reads isolated external-target output and default `format=brief` returns a Markdown/YAML agent brief.                                                                                                                                                                                                                                                                                                                                                                                                                |
| `inspect_symbol(symbol, format?, target_root?, project?)`                                                                                  | Inspect one symbol/component/function from existing artifacts; default `project=MAIN` keeps target-repository editing scoped to the main project. Use `project=all` or a scoped `PROJECT::Symbol` only for explicit merge/variation review. `target_root` reads isolated external-target output and default `format=brief` returns a Markdown/YAML agent brief.                                                                                                                                                                                                                |
| `get_watchdog_session(human_report?, target_root?, profile_id?)`                                                                           | Read the latest persisted target-repository watchdog pulse. The public profile does not authorize `sage_self`; analyzing SAGE source still uses ordinary target-repository identity.                                                                                                                                                                                                                                                                                                                                                                                           |
| `get_active_signals(scope?, include_bodies?, max_files?, max_chars_per_file?, format?, target_root?)`                                      | Read bounded ContextOS L1/L2 signals with strict masking. Only `current_change_scope=bounded` plus `current_turn_claim=supported` establishes current-turn focus; otherwise the packet is persisted orientation. `target_root` reads isolated external-target output and fails closed when signals are missing.                                                                                                                                                                                                                                                                |
| `trace_upstream_cause(target_node, target_root?, format?)`                                                                                 | Trace likely upstream causes for a downstream target from active ContextOS signals and dependency edges. `target_root` reads isolated external-target output; missing target graph evidence fails closed instead of falling back to the SAGE workspace.                                                                                                                                                                                                                                                                                                                        |
| `get_surgical_operation_packet(max_signals?, format?, target_root?)`                                                                       | Load the default Markdown/YAML surgical brief for editing the analyzed repository; `target_root` reads isolated external-target output, required inputs are content-hash-bound to the active analysis snapshot, optional unbound inputs are omitted and reported as incomplete evidence, `format=json` returns the canonical machine contract, and `format=brief_debug` includes internal graph references.                                                                                                                                                                    |
| `get_violation_work_queue(page?, page_size?, rule?, project?, severity?, target_root?, format?)`                                           | Read a paginated target-repository technical-debt queue from SAGE Audit violations. `clean_within_sage_audit` with partial coverage is not repository-wide cleanliness or target-native compliance.                                                                                                                                                                                                                                                                                                                                                                            |
| `validate_actor_proposal(proposal, target_root?, request_id?, request_actor_id?, request_purpose?, repository_snapshot?, declared_scope?)` | Validate a proposal's request identity, actor identity, snapshot reference and category-specific affected scope. Use it through `dispatch_actor_request(operation="propose")`; canonical request fields are injected by the gateway. `PROPOSAL_CONFORMANT` is not authorization, technical validation or acceptance.                                                                                                                                                                                                                                                           |
| `dispatch_actor_request(request, tool_name, tool_arguments?)`                                                                              | Dispatch a canonical purpose/operation/scope request only to an eligible tool visible in the active MCP profile. The bounded scope supports `get_violation_work_queue` for `inspect`, the snapshot-bound `get_surgical_operation_packet` for `advise`, `validate_actor_proposal` for non-authorizing `propose`, and `validate_patch` for `validate`; it returns canonical terminal `status`, separate `completed_states`, the exact raw `tool_result`, and `INCOMPLETE_EVIDENCE` for unknown or failed dispatch. Legacy direct tool calls remain partial Actor Contract paths. |
| `external_target_preflight(target_root)`                                                                                                   | Validate an external target folder.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `search_symbols(query, project?, target_root?, format?, evidence_plane?)` | Default `evidence_plane="indexed"` preserves normal symbol/file/derived search, MAIN scope and machine-list compatibility. Explicit `evidence_plane="declaration"` searches retained named declaration headers for one exact project only, returning a bounded metadata envelope even when unavailable/empty. No live source, resolved types, runtime or edit authority. Follow exact declaration refs with `inspect_file`, not `inspect_symbol`. Selected-primary verification still reads primary bytes; it is not a measured cheap-I/O mode. |
| `check_module_integrity(module_path, target_root?, format?, max_items?)`                                                                   | Filter audit violations for a module or path. `target_root` reads isolated external-target audit evidence and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `get_surgical_context(symbol, project?, target_root?, format?)`                                                                            | Build a concise symbol briefing from atlas data. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `get_dead_code(path?, target_root?, format?, max_items?)`                                                                                  | Read dead-code findings, including public-contract protections. `target_root` reads isolated external-target output; default `format=brief` returns a bounded Markdown/YAML context brief and fails closed when the target artifact is missing. A grounded empty filter result is explicit `no_actionable_items`/`no_action`, keeps candidate evidence fields non-applicable, and never claims that the repository is clean.                                                                                                                                                                           |
| `find_clones(symbol, target_root?, format?, max_items?)`                                                                                   | Inspect clone/duplication evidence for a target symbol. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `get_health_metrics(path?, target_root?, format?)`                                                                                         | Read health and risk summaries. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `get_circular_dependencies(module?, target_root?, format?, max_items?)`                                                                    | Inspect circular dependency evidence for a module or whole target. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                       |
| `get_blast_radius(symbol, target_root?, format?, max_items?)`                                                                              | Inspect expected impact of a change. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `get_state_flow(project?, max_items?, full?, target_root?, format?, file?, symbol?)` | Follow-up-only: use `target_repository_followup` for a concrete question. Exact case-sensitive project/file/symbol focus supports `DeclaringSymbol.member`; no selectors preserves the project summary, which now reports stored Atlas snapshot matching only when the requested project was analyzed, or explicit unavailable status; this is not a live-source check. Broad transitive hook sources require an indexed top-level callable's parser-bound direct named-import call and one same-project provider module; a mere import does not transfer the provider module's query/mutation ownership. The link stays file-level advisory, absence remains unproven, and an old import-only artifact is unavailable until refreshed. Initializer members are syntax candidates, not proven factory-result properties; snippets still ground the declaration. Legacy import candidates remain shadow-blind references. `symbol_context.import_calls` records single-file lexical import binding for body call syntax. For exactly checker-bound named-import or namespace-qualified Zustand `create`/`createStore`, including a zero-argument curried factory and at most one equally checker-bound named-import or namespace-qualified `persist` wrapper from `zustand/middleware`, `symbol_context.setter_calls` records bounded direct setter-call syntax under an indexed action candidate. `factory_form` and `middleware_form` label the parsed shape; inspect omissions and source binding. Each call's bounded `literal_state_keys` shows only top-level identifier/string keys in a direct object first argument. Updater callbacks, spread/computed/shorthand/special keys and old evidence remain unavailable; observed keys are argument syntax, not executed writes. Type-only/shadowed imports, chained or other middleware, indirect/nested setter uses and old Atlas evidence remain unresolved. Do not infer execution, a state transition or persistence. `direct_callees` joins a call to one direct exported callable in the same Atlas project only when unique. Its `target_context_index` points into deduplicated `target_symbol_contexts`, one bounded view of that target's recorded signals, references and call syntax. Its outbound_calls may identify one further same-project direct-export syntax candidate under the shared budget, without a third target context; all three file endpoints require independent source binding, and no hop proves execution or persistence. Inspect statuses, reasons, omissions and both endpoint bindings. Nested or indirect calls, ambiguous exports and re-exports remain unresolved; matching source bytes do not prove execution, resolver freshness, parameter effects or persistence. For a selected supported Zustand action, upstream_action_calls may identify direct store.getState().action() or const { action } = store.getState(); action() syntax in an indexed same-file callable only; source binding, omissions and scope remain explicit. It excludes aliases, shadowing, computed/optional calls, nested callables and hook subscriptions, and never proves caller execution or an actual write. For a selected persisted Zustand action, rehydrate_call_candidates shows capped direct store.persist.rehydrate() or same-callable const-local alias invocation syntax, plus direct calls in one inline callback argument, checker-bound to the exact exported variable. Inspect call_form and call_context, omissions and both source bindings; mutable, chained, optional, computed, shadowed, non-inline or deeper nested, argument-bearing and old-parser cases remain unavailable. A call candidate never proves hydration, storage I/O or callback execution. cross_file_rehydrate_call_candidates reports separately capped direct named-import store.persist.rehydrate() or same-callable const-local alias invocation syntax, plus direct calls in one inline callback argument only for one unique same-project exported persisted store. Inspect call_form and call_context, importer_lookup, ambiguity, omissions and independent store/caller live-byte binding; type-only, mutable, chained, shadowed, optional, computed, argument-bearing, non-inline or deeper nested and legacy forms remain unproven. This is a source candidate, not executed hydration or storage success. cross_file_action_calls additionally maps those direct or const-destructured named-import calls only when the same-project module and named store export are unique; inspect its ambiguity, omission and independent caller/store source bindings. importer_lookup identifies a file-identity-digest-checked same-snapshot producer index or bounded fallback scan; the index narrows candidates only, and missing/stale index evidence never proves absence. hook_selector_candidates additionally shows only parser-bound direct store((state) => state.action) selector syntax for a named Zustand create hook with unique same-project store identity. A positive `selected_result_direct_calls` entry records direct calls of a const-bound selector result in the same indexed callable, capped at eight sites per selector with explicit omitted count; a missing entry does not prove absence. For a named exported use-prefixed callable, `hook_return_owner` records only a root-level direct return statement or synchronous concise-arrow result of that selector; indirect, conditional, async and private returns remain unjoined. Alias, mutable, optional, shadowed and nested-callable invocations remain outside this evidence. It excludes vanilla createStore, shadow/type-only/alias/dynamic/optional/nested forms; legacy evidence and ambiguous imports stay unavailable. Independent caller/store source bindings do not prove a subscription, render, executed action or state transition. A selected direct named-export callable, including a returned hook, may expose upstream_direct_import_calls from exact same-project named imports in indexed callable bodies; query the hook file/symbol separately after hook_return_owner. This second syntax hop has a shared scan/output budget and independent selected/caller byte checks. Optional, shadowed, type-only, nested and ambiguous calls are not joined; no hook invocation, render or action execution is proven. same_file_direct_calls reports only checker-bound direct identifier calls in indexed callable bodies of the selected file, tied to one top-level callable declaration. The parser caps each caller at 64 sites; a cap, legacy evidence or shared-budget exhaustion is incomplete, never proof of absence. Shadowed, optional and nested calls are excluded; current file bytes still do not prove execution. For a selected direct exported node:events EventEmitter singleton, event_bus_calls exposes literal named-import .emit/.on/.once/.off/.addListener/.removeListener syntax only when one same-project module and current producer/caller file bytes match; inspect importer_lookup and omissions. Anonymous nested callbacks and indexed class/direct-object members are labelled syntax-only; caller_member and caller_owner_kind identify the recorded owner syntax. Indirect returned objects and general instance-property receivers remain unjoined. Selecting a named exported variable may expose imported_property_event_calls: checker-bound named-import value.property literal event-method syntax from one same-project module, with at most 64 sites per indexed caller, a file-level parser-coverage marker and independent caller/export byte checks. This does not bind the variable's factory result, conditional package exports, singleton cache, a selected Class.field, listener delivery or execution. A readonly class field directly initialized from one checker-bound named import can expose capped literal this.field event-call syntax in indexed methods, with partial coverage on legacy evidence. For that imported-field join, mutable/reassigned, type-only, alias, computed, optional and nested forms stay unresolved. Separately, selecting a class property that directly constructs a checker-bound node:events EventEmitter may expose class_field_event_calls for literal this.field calls in direct methods or lexical arrow callbacks. This lane is capped at 64 producer calls under the shared query budget and selected-file source binding; direct `this.field` reassignment fails closed, and external object.field callers stay unresolved. Inspect callable_nesting and source_binding; a mutable/public field never proves runtime instance ownership. Runtime instance ownership, listener registration, delivery and callback execution remain unproven. Module resolution freshness and runtime effects remain unproven. Missing/old evidence remains unavailable until ordinary refresh. File signals and empty results do not prove symbol flow or absence. `target_root` stays isolated; focused `full/json` retains every budget. |
| `get_hexagonal_bindings(target_root?, format?, max_items?)`                                                                                | Inspect port-adapter bindings. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `get_merge_review_queue(action?, max_items?, target_root?, format?)`                                                                       | Review human-approval-gated merge/import candidates as a bounded Markdown/YAML queue. It is advisory only and must not be used to apply file copies or imports automatically.                                                                                                                                                                                                                                                                                                                                                                                                  |
| `get_ui_architecture(component?, max_items?, full?, target_root?, format?)`                                                                | Inspect UI architecture and component surface evidence. `target_root` reads isolated external-target output; default `format=brief` returns a bounded Markdown/YAML context brief. Use `full=true` or `format=json` only for machine inspection.                                                                                                                                                                                                                                                                                                                               |
| `validate_patch(target_file, patch_content, target_root?, format?, actor_id?, patch_input?)`                                                             | Validate a proposed patch in memory before writing it to disk. `target_root` validates path safety and current content against the selected target repository root; when `actor_id` is supplied, it must own a current matching target-write lease; default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                              |
| `manage_target_write_lease(action, target_file, actor_id, target_root?, ttl_seconds?, format?)`                                            | Follow-up-only acquire/release protocol for cooperating agents before and after an external patch attempt. It is SQLite-backed coordination within one SAGE target-analysis deployment, not a cross-deployment filesystem lock.                                                                                                                                                                                                                                                                                                                                                |
| `get_impact_radius(target_node, target_root?, format?, depth?)`                                                                            | Read impact radius evidence for a target node. `target_root` reads isolated external-target output and default `format=brief`, `depth=2` returns a bounded Markdown/YAML directive with shown/omitted dependent counts. Use `depth=3` only when direct evidence cannot explain a failure or the human asks for broader impact. `depth=0` is target-reachability debug, not a whole-repository graph export: SQLite counts at most 128 hops and returns at most 200 transitive rows (the direct list is separate); Atlas fallback reaches only its loaded graph. Missing dependency graph evidence fails closed. |
| `get_test_impact(target_file, target_root?, format?)`                                                                                      | Identify tests likely affected by a changed file. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `get_confidence_score(target_file, target_root?, format?)`                                                                                 | Read confidence/risk scoring for a target file. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                          |

For an exact selected named-export variable, `factory_source_candidate` records only
checker-bound local factory-call or factory-argument syntax and that factory's
direct `new` return of a named import. A wrapper may bypass the factory; package
export conditions, runtime instance identity, public-field mutation and event
delivery are not established. Missing strict evidence is not proof of absence.

For a selected supported Zustand action, `persist_storage_candidate` may bind
one literal `persist` storage option to a named import and an exact same-project
module file. Its nested `export_candidate` reaches `target_candidate` only when
that file has one indexed direct named variable export with the imported name. Check both
source bindings and any unavailable, ambiguous or incomplete status. This is
source syntax, not proof that the export implements a storage adapter, that the
action ran or wrote data, or that hydration completed. Dynamic options, re-exports,
missing/legacy evidence and runtime conditions remain unresolved.

SAGE developer/debug, release-proof, provenance, capability, pipeline,
telemetry, learning-system and workspace-maintenance tools are maintainer-only
surfaces, not part of this public target-repository coding contract.
`dag_parallel_safe` means a step may run after dependencies are satisfied;
`sequential_required` means final/broad artifact consumers should see settled
upstream outputs; `full_run_only` means the step is not a watchdog default
unless explicitly requested.

`search_symbols` also returns bounded `QualifiedImportCallCandidate` rows for
parser-bound direct namespace-import member calls inside indexed top-level
callable bodies and module-root expressions. Static string-literal bracket
members such as `Sentry["init"]()` use the normalized `Sentry.init` search key;
the source span shows the original syntax. These are search-only syntax
candidates, not declarations, dependency edges, module resolution or runtime
execution. Calls inside nested callable bodies or classes, optional/dynamic-computed
forms and other import kinds are outside this slice. Inspect file and coverage
before drawing conclusions; capped, partial or legacy coverage is not evidence
of absence.

When a SAGE packet surfaces a validation command, inspect its
`explicit_step_closure` or `validation_command_contracts` before assuming the
command is narrow. Some explicit steps execute broad or global dependency
closures; use them only when the packet or human asks for that proof envelope.

## Default Workflow

1. Initialize the repository explicitly with `python sage.py init --target-root <repository>`.
2. Refresh bounded target evidence with `python sage.py run --target-root <repository> --profile daily --projects MAIN`.
3. Use the default MCP profile for the surgical packet, search and inspection.
4. Switch to `target_repository_followup` only after a concrete supporting or HITL need exists.
5. Pull closure/genome evidence before copying or restructuring code.
6. Check approval gates, audit, risk and blast-radius signals before invasive edits.
7. Re-run the relevant target-bound pipeline step after changes.

An explicitly configured embedded/default repository may use the corresponding
targetless forms. A central installation keeps `--target-root` on every
repository-specific command.

## Usage Guidance

### Large patch input

`validate_patch` also accepts optional `patch_input` with empty `patch_content`.
This local file mode avoids putting large Unicode payloads in shell argv; it
does not add another validator or grant apply authority. Send the small envelope
through the existing structured MCP client:

```json
{"version":1,"mode":"workspace_file","path":"change.diff","byte_count":123,"sha256":"<raw patch SHA-256>","source_sha256":"<current target raw SHA-256>","analysis_snapshot_id":"<current Atlas snapshot>"}
```

Replace every placeholder and compute the integer byte count from original
binary bytes, not rendered terminal text. Use binary file APIs when preparing or
copying the patch (Python `read_bytes`/`write_bytes`, PowerShell
`[IO.File]::ReadAllBytes`/`WriteAllBytes`); text redirection/default encodings and
base64-in-argv do not establish byte fidelity. The caller owns its payload file
and cleanup. The explicit path is relative to the selected target root and must
be visible on the server's local filesystem, regular, unlinked and contained.
No arbitrary outside-root reads, remote upload or payload execution is provided.

File-v1 admits at most 2,000,000 bytes each for patch and source, strict UTF-8,
and one existing indexed target with unambiguous `--- a/<target>` and
`+++ b/<target>` unified-diff headers. Quoted/ambiguous paths, binary, rename,
multi-file, creation/deletion, mode-only and replacement payloads are unsupported
in this mode. Existing inline behavior is unchanged. Preserve CRLF/LF and Unicode
normalization; do not strip a patch BOM or repair a malformed input silently.

`patch_input_receipt` separates byte admission and sampled source/snapshot
continuity from logical governance, exact non-mutating Git applicability and
target-native validation. An input refusal is not a target defect. The logical
parser is not byte-exact resulting-file proof; a matching digest is not proof of
human intent, a hostile-filesystem sandbox or apply-time continuity. Keep existing
actor/profile, lease, seal and target-native obligations; `safe_to_apply` is not
enabled by transport success. A failure before the server receives the request
has no server receipt. Do not claim otherwise or retry the large text via argv.

- **ONLY use the unified public CLI**. Use `python sage.py ...`; do not call internal implementation files directly:
  - `python sage.py init --target-root <repository>` (Prepares explicit target truth)
  - `python sage.py install-proof --level release --target-root <repository> --projects MAIN` (Bounded installation proof)
  - `python sage.py run --target-root <repository> --profile daily --projects MAIN` (Bounded analysis)
  - `python sage.py watch --target-root <repository>` (Live monitoring)
  - `python sage.py doctor --include-validate --quick` (Installed-product health)
  - `python sage.py mcp --print-config --profile target_repository_default` (Primary AI surface)
  - `python sage.py show-config` (Compiled local runtime truth)
  - `python sage.py external-targets` (External-target run index)
  - `python sage.py purge --mode standard` (System cleanup)
  - `python sage.py backup` / `python sage.py restore` (Local SAGE state administration)
- Use the public MCP profiles for surgical briefs, bounded inspection, HITL requests,
  patch validation, target-local lesson projection and agent-response recording.
  Private SAGE maintainer CLI/MCP/reality profiles are not part of the public product surface.
- Use symbol search before opening many files manually.
- Use closure bundles before migrations or donor extraction.
- Use audit and safety outputs to explain risk, not just to block work.
- Prefer compiled runtime config and generated artifacts over hand-written assumptions.
- Treat `React Runtime Intelligence` as broad static repository analysis. It
  does not run target tests or ingest their stderr; runtime warnings require
  target-native execution and source corroboration before a production edit is proposed.
  A source-bound JSON field-to-typed-state candidate can include a literal
  fallback and lexical branch; neither proves the field type or absent upstream
  validation. Inspect the exact adapter/response contract before prescribing a
  parser or edit. Unsupported paths and an empty candidate list are not clean proof.
  `inspect_file` may show separate `client_local_parser_result_evidence` and
  `client_local_parser_input_sink_evidence` sections for a finite same-file
  fetch/JSON/parse-call/setter sequence. The latter records this sink selecting
  the JSON input rather than the call result, not a validation bypass: the call
  can reject, mutate the input or return an alias. Neither section proves a
  schema, compatible runtime type, execution or edit authority. Both share a
  compact budget and record cap; inspect source binding, shown/omitted counts
  and unavailable reasons. Empty records do not prove clean or complete coverage.
  `client_imported_parser_evidence` separately records a runtime named-import
  `.parse` call and whether this setter selects its result or its JSON input.
  A `parser_source_candidate` is only one unique same-project direct variable
  export with independent snapshot/live identity, not proof of schema behavior,
  SDK semantics or HTTP endpoint correspondence. Follow its exact source range;
  unresolved/barrel/ambiguous/drifted candidates must not be guessed or treated
  as safety. These lanes share the compact budget and disclose omissions;
  absent legacy metadata is not an observed-empty new lane.
  The separate `response_json_return_evidence` lane records only an exact
  top-level TS/TSX async fetch/JSON/return three-statement body. It preserves a
  whole JSON value versus literal field path and one outer type assertion.
  Source-bound returned-expression syntax is not schema validation, resolved
  Promise data, callback execution or caller-state correspondence. This lane
  shares the same compact budget/cap; follow its exact source range and omissions.
  Focused `get_state_flow` can additionally show separate
  `symbol_context.local_arrow_import_calls` and `local_arrow_import_callees`.
  These record named-import calls inside one immediate const local arrow with
  its lexical owner, not calls executed by the selected outer callable. A unique
  same-project direct-export adapter can expose only its exact selected
  `response_json_return_evidence`, matched by name, declaration kind, byte span
  and source/AST identity. Inspect both independent source bindings, shared
  budgets and omissions. Anonymous/deeper callbacks, branches, shadowed/type-only
  bindings, legacy or ambiguous metadata remain unresolved. The join does not
  prove arrow invocation, Promise data, schema validity or a caller-state update.
  A separate `symbol_context.local_arrow_result_uses` lane records only an
  immediate async const arrow's two-statement shape: `const result = await
  namedImport(...); if (result.property) { boundIdentifier(result.property); }`.
  Inspect the unique lexical binding, exact caller UTF-8 spans, source identity
  and `callee_orientation`. The caller's `selected_property` and adapter's JSON
  `field_path` remain separate; never concatenate them into a runtime path.
  A truthiness guard is not validation and a sink named `setTables` is not proved
  React state mutation. Casts, aliases, optional/dynamic/chained reads, else or
  additional statements remain outside this finite lane. Caller drift clears
  usage; adapter drift leaves caller syntax but removes the cross-file association.
  Shared-budget exhaustion omits new rows before disturbing existing call/return
  evidence. Missing/omitted rows are not proof that no such use exists.
  `symbol_context.local_arrow_sibling_calls` separately records a direct plain
  or awaited identifier call between two uniquely bound immediate const block
  arrows under the same indexed outer callable. Caller and target are local
  declarations with exact UTF-8 spans, not invented indexed callee references.
  Inspect source identity and omissions; shadowed/mutable/alias bindings,
  optional/member calls, branches and deeper/deferred callbacks are excluded.
  This lane proves neither invocation nor argument/result transfer, Promise
  resolution, schema validity, state mutation or error dominance. Source drift
  clears the lane; shared-budget exhaustion omits it before existing evidence.
  `symbol_context.jsx_prop_callbacks` adds site-specific JSX source candidates:
  one checker-bound named-import tag, a literal prop whose bare value is a unique
  immediate const block-arrow, and the same prop's simple child parameter call.
  Inspect the separate parent and child UTF-8 spans and direct-export identity.
  At most two inline JSX attribute arrow contexts are retained. Spreads, aliases,
  defaults/rest, writes, ambiguous/re-export targets and unsupported deferred
  callbacks remain outside this lane. Both files must independently match their
  snapshots and live bytes; parent drift clears the lane and child drift removes
  the association's child records. Legacy metadata is not fabricated as empty.
  Its endpoints also require exact verified primary Atlas metadata; a compatibility
  shadow cannot rescue missing/corrupt primary evidence. Focused State Flow now
  requires content and identity from one verified primary read for every syntax
  lane; legacy or failed primary reads remain unavailable, not observed-empty.
  Its endpoints reuse that request's verified metadata under the same storage
  and scan budgets. Inspection uses exact selected-primary reads, never a warm
  mutable global Atlas dict. Explicit default JSON-primary mode remains primary
  even with a residual SQLite database. Legacy recovery/orientation APIs remain
  separate. Selected-file reads may verify/spool the whole primary payload;
  cross-request full-content validation remains required and no constant-time,
  aggregate speedup or token-saving claim is established. A storage checksum
  does not attest the original AST producer or replace snapshot/live-byte checks.
  Shared scan, item and compact token budgets disclose omissions; empty records
  do not prove no callback exists. A source candidate proves neither React
  activation, callback invocation, argument/result transfer nor runtime/schema
  validity. It does not establish one global prop value across all JSX sites.
  `symbol_context.returned_local_callbacks` separately associates a unique
  immediate const initialized by a bound named React `useCallback` with its
  shorthand in one direct returned object. Named import aliases, asynchronous
  block callbacks and plain Unicode identifiers retain exact UTF-8 source spans;
  callback arguments/body/dependencies are not interpreted. Inspect selected
  primary metadata, snapshot/live binding and shared scan/item/token omissions.
  Local endpoints are not indexed callees. Writes, object spreads/renamed keys,
  indirect/branch returns, type-only/shadowed imports and legacy/malformed
  evidence remain outside this finite lane. Neither callback value, activation,
  schema validity nor runtime execution is established. Omitted/empty records
  do not prove no callback exists.
  A separate symbol_context.call_result_property_bindings lane records literal
  properties destructured into immediate const local identifiers from one
  direct named-import call, including renamed properties/import aliases.
  Inspect exact UTF-8 pattern/import spans, verified primary and snapshot/live
  source binding, and shared scan/item/token omissions. Writes, defaults, rest,
  computed/quoted/nested keys, optional calls and old or unbound metadata remain
  unavailable. These are local source facts, not indexed callees: property
  existence, callable value, callback activation, schema validity, semantic
  value transfer and runtime execution are not established. Empty/omitted
  results do not prove absence.
  Separately, `symbol_context.call_result_alias_invocations` associates a later
  immediate plain identifier call with that exact const binding, including a
  renamed local. Inspect its 17 UTF-8 source spans, current primary and
  snapshot/live binding, and remaining shared scan/item/token omissions.
  Nested/conditional, before-binding, optional/generic/spread/wrapped calls and
  mutable/ambiguous bindings are not joined. The imported producer is not the
  invoked target; callable identity, value transfer, callback activation, schema
  validity and runtime execution remain unproven. Empty/omitted records never
  establish absence.
  `symbol_context.call_result_optional_alias_invocations` separately associates
  a later immediate `run?.()` call with that admitted const binding. Inspect its
  18 UTF-8 spans, including the exact `optional_operator`, current primary and
  snapshot/live identity, and remaining shared scan/item/token omissions.
  Ordinary and optional calls share one bounded owner-body scan. Conditional,
  nested, returned, generic/spread/member/wrapped, written and ambiguous forms
  remain unjoined. Optional syntax proves neither short-circuit outcome,
  callable identity, argument evaluation, value/schema nor execution. The
  imported producer is not the invoked target; omitted detail is not absence.
  `symbol_context.returned_callback_parameter_optional_calls` separately links
  an already oriented returned React async callback's parameter-property const
  binding to `await callback?.(argument)` in an immediate own try block. Inspect
  all 35 UTF-8 spans, exact old anchor, current primary/snapshot-live identity
  and shared omissions. `parameter_property_name` is not the returned object's
  `property_name`. Unselected primitive defaults describe syntax only. Selected
  or effectful defaults, nested/rest/computed/quoted/duplicate bindings, writes,
  shadows, conditional/deferred/returned/catch/finally/deeper calls and generic,
  member/wrapped/spread forms remain unjoined. Argument spelling is not value,
  HTTP/JSON or schema proof; callability, activation, reachability and execution
  remain unknown. New detail is dropped before old lanes; empty is not absence.
  The separate `symbol_context.returned_callback_parameter_context_calls` lane
  adds plain own-try calls, plain/awaited own catch/finally calls, and one
  immediate braced `if` then/else inside those bodies or the callback body.
  Inspect its exact 34–40 UTF-8 roles and owning `if`/`else` selector token;
  `await_expression` is present only for awaited syntax. The old awaited-try
  lane remains exclusive. Same parameter/binding guards, complete returned
  anchor, verified primary/current bytes and remaining shared budgets apply.
  Deeper/unbraced/else-if/loop/deferred/returned or shadowed/written forms remain
  unjoined. Lexical context proves neither reachability, exception origin,
  activation, callable/value/schema correspondence nor execution. This lane
  settles last; dropped rows and missing metadata never prove absence.
  The additive `symbol_context.returned_callback_parameter_unbraced_if_calls`
  associates the same admitted parameter binding with one immediate unbraced
  `if`/`else` optional identifier call, plain or awaited, in the callback or
  its own try/catch/finally body. Its exact 36–40 roles use `branch_statement`
  (equal to `invocation_statement`), never reinterpret the older `branch_body`.
  Require `coverage: finite_source_grammar_only`, current primary/source identity
  and shared budgets. Zero records or truncation omissions do not prove complete
  source coverage. Else-if/deeper/deferred/written/shadowed forms remain unjoined;
  activation, callability, value/schema and runtime effects remain unproved.
  This new detail settles after the older lanes and is dropped first.
  `symbol_context.call_result_nested_callback_alias_invocations` separately
  associates that outer const binding with a plain call in a synchronous block
  arrow property of one immediate literal object call argument. Inspect all 25
  UTF-8 spans, primary/live binding and shared omissions. Container callee is
  spelling only. Spread and duplicate static-key flags describe syntax, not
  effective configuration; computed keys remain unknown. Registration, callable
  identity, callback activation, value/schema and execution remain unproven.
  Conditional/deeper/async/method/concise, optional/generic/spread-call, shadowed
  and mutable forms are unjoined; missing evidence never establishes absence.
  `symbol_context.parameter_config_callback_forwardings` separately associates
  a simple owner parameter or shallow parameter property with an immediate const
  property binding and its later optional call forwarding one exact callback
  rest parameter. Direct input or literal `|| {}`/`?? {}` fallback and an optional
  outer empty-object parameter default are source syntax only. Inspect 27 UTF-8
  spans, primary/live binding and shared omissions. Object-rest exclusion is not
  proof that a later spread overrides the callback. Defaults inside binding
  patterns, nested/computed/quoted/ambiguous/mutable forms, branches, generic or
  nonoptional calls and different arguments remain unjoined. This parameter
  provenance is not an imported producer or call-graph edge; callability, value
  transfer, registration, activation, schema and execution remain unproven.
  Missing, stale or omitted detail never establishes absence.
  `symbol_context.call_result_optional_rest_alias_invocations` separately binds
  an admitted named-import call-result const alias to an optional identifier
  call forwarding the exact rest parameter of one literal-object synchronous
  block-arrow callback. Inspect 29 UTF-8 source spans, verified primary and
  snapshot/live identity and shared scan/item/token omissions. Imported result
  provenance is distinct from parameter configuration. Rest/optional syntax,
  object spread and duplicate-key flags establish neither argument values or
  cardinality, effective registration, callable identity, activation, schema nor
  execution. Conditional/deeper/async, indirect, generic, written or ambiguous
  forms remain unjoined. Missing, legacy and omitted detail is not absence proof.
- Audit shallow-copy/nested-index-write review candidates preserve the copy,
  write and awaited-call spans from one bounded async body. The call is not
  assumed to persist anything. Verify property ownership, accessors/proxies,
  intentional sharing and failure behavior with target-native evidence before
  proposing a change. `needs_runtime_proof` and an empty candidate list are not
  runtime confirmation or completeness proof.
- Test Impact also declares `native_diagnostics: not_ingested`; it does not
  inspect a target test run. Check both native output streams separately from
  the exit code. Nonempty stderr is not automatically failure, empty stderr is
  not proof of clean execution, and a warning near a component may come from
  its test double or provider setup rather than production code.

## Safety Guidance

Existing native logs may be identity-ingested with
`python -B tools/ingest_native_run_logs.py --target-root <repo> --bundle-root <logs> --manifest <relative-json> --output <receipt-outside-input-roots>`.
The input format is `config/schemas/native_run_log_manifest.schema.json`.
Supply both streams and their hashes, reported run/tool/argv/cwd/times/exit code,
and declared source/config hashes. Target and bundle paths are bounded; nothing
is executed. `INGESTED` establishes stream-byte correspondence only. Matching
current source/config bytes by itself does not authenticate a historical run or establish
Atlas lineage, full input capture, clean diagnostics or changed-effect coverage.
The receipt persists hashes and bounded metadata, not raw logs/source/argv.
Missing metadata is not synthesized for legacy logs. Optional
`--diagnostic-format vite_5_4_21_mixed_import_console_v1` recognizes only the
declared Vite 5.4.21 single-line mixed static/dynamic import warning. It consumes
the same captured matching bytes, deduplicates exact warning messages across
both streams and reports hashes plus log line locations, not raw messages or
inferred source paths. Unsupported versions, unreadable input and budget limits
are explicit; zero observations never means clean diagnostics. Reported warning
severity is independent of exit code. Source attribution, historical execution,
bundle performance and runtime coverage remain unproven; Test Impact stays static.
For this format, optional `source_correspondence` compares only exact canonical
absolute identifiers with already declared source inputs. References are zero-based
indices into `inputs.source` in the same receipt, bound to its input-observation
identity; read current `MATCH`/`MISMATCH`/`UNAVAILABLE` from that input row.
Log paths are never resolved, opened or added to the input set. Unknown, relative,
virtual and query/fragment identifiers remain unresolved. Unescaped comma-space
importer lists and repeated reporter separators remain explicitly ambiguous.

Optional `--atlas-raw-dir <existing-storage-root> --atlas-project <exact-key>`
corroborates every declared source input against one validated Atlas snapshot.
The storage root must be `.raw`; receipt output must remain outside it.
It reuses the original bounded byte read and exact UTF-8 text hash; it never
refreshes Atlas or reopens a log-reported path. The project root must match the
recorded target identity. Missing/stale/mismatched source, unsupported identity,
empty scope or invalid Atlas yields unavailable/blocked evidence, never a
timestamp fallback. The existing analysis lineage writer binds the exact receipt
content to that consumed snapshot; opt-in CLI exits nonzero if lineage is blocked.
`COMPLETE` means only analysis-time declared-source text correspondence. The
raw receipt's run/Atlas authority stays unestablished: config, complete compiler
inputs, historical execution, diagnostic causality and runtime coverage are not
attested. No automatic Audit, Test Impact or Quality Gate enforcement is added.
A matching identifier is not an import edge, historical execution or source fault.

- Do not edit `codemaps.discovery.json` directly.
- Prefer editing `codemaps.overrides.json` for human policy decisions.
- Do not assume a repository uses the same project names, doctrines, or module roots as this one.
- Do not execute generated mutation, merge, delete, move, or refactor actions without explicit human approval.
- Treat `output/scripts/auto_merge.*` and `output/scripts/auto_heal.*` as human-supervised operator artifacts, not normal target-repository coding context.
- `auto_merge` is a filtered merge/migration plan backed by manifest, checksum, backups and rollback; it still requires HITL review before execution.
- `auto_heal` is filtered by the generated-script policy; script-ineligible findings must appear as skipped/manual-review manifest evidence and must not be recommended to a coding agent as a default batch fix path.
- If a work queue suggests many similar fixes, inspect or validate a bounded target first; only mention generated scripts as an optional HITL-gated operator path with manifest evidence.
- Audit presentation order is not root-cause priority or edit authority. The work queue orders indexed rows before pagination; Surgical/Operator order producer-bound Audit rows before their cap, keeping Audit ahead of other source groups. Missing row severity stays unknown. Missing recorded indexed identity or an invalid descriptor preserves packet input order with `presentation_order.status=unavailable`; the SQLite queue does not substitute a JSON fallback. A shared descriptor or Atlas snapshot does not establish equal candidate coverage. Positional directive IDs are not persistent finding identity or reusable approval.
- Hexagonal JS/TS findings may additionally carry `finding_evidence`: exact
  captured top-level named/default/namespace/side-effect import records, verified
  against the selected Atlas, source snapshot, finding generation and applicable
  SAGE path-model policy. This is a record set, not individual import occurrences:
  source ranges and independently resolved target endpoints are not established.
  `partial` discloses the bounded record cap; `unavailable` includes legacy,
  unsupported, missing, malformed or stale evidence without removing the finding.
  This does not prove target-native policy, runtime behavior, causality, absence,
  architecture intent or safe mutation. Keep the existing approval/trust boundary.
- The existing declared-layer import restrictions may carry the same optional
  field with explicit v2 rule/decision evidence; Hexagonal v1 is preserved.
  Inspect its exact restriction row and first matched fragment, not display
  prose alone. Source/policy/generation must verify and the complete finding
  must match selected SQLite primary state. Caller-resealed metadata, missing
  source, unsupported forms or conflicting traces remain unavailable while
  ordinary findings/counts stay visible. This does not expand language scope,
  establish target-native import legality or grant mutation authority.
- Existing cross-module and own-API path findings may additionally carry v3
  source/rule evidence. It names the module container actually consumed by
  Audit, the raw alias/path decision and any evaluated API-entry exemption.
  Source-module matching uses the first exact contiguous container path sequence,
  not a substring or a guessed architecture; multi-component roots are supported.
  The declared alias/container prefix must start the raw import specifier;
  embedded alias text in a foreign specifier does not establish module ownership.
  Verify selected source, configuration, policy and complete SQLite finding;
  do not rediscover module topology on the host or treat a literal `/api`
  match as package-export legality. Legacy/corrupt/unsupported evidence stays
  unavailable without erasing ordinary findings. Existing approval rules apply.
- Before asking for approval, use the public target-bound MCP lifecycle to create a HITL decision request with evidence and scope.
- When approval is granted or denied, use that lifecycle to record it in the HITL approval ledger before acting, then close the request.
- Keep AI answers in the Nexora response shape: verdict, evidence, confidence, risk, human_approval_required, suggested_next_action, source_artifacts.
- Record high-stakes AI answers through the public target-bound response tool when they will guide human approval or code changes.
