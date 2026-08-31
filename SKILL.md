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

## Target Repository Path Contract

- Open files with `analysis_root + target_file`, `analysis_root + related_files`
  or `analysis_root + inspect_first` entries.
- Treat `target_ref` values such as `MAIN::src/App.tsx` as SAGE/MCP follow-up
  references only. They are not filesystem paths.
- `target_ref` format is `<project>::<repo_relative_path>`. `MAIN` means the
  primary analyzed project/workspace scope; variation or external project
  prefixes are explicit scopes, not folders to open directly.
- Search results are candidates, not patch instructions. Use the returned
  `next_tool` or call `inspect_file` / `inspect_symbol` before editing.
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
- `get_test_impact` returns repo-relative test files and commands. Run listed
  tests from the target repository only when the command is clearly a
  target-repository package script; SAGE validation commands run from the SAGE
  workspace or through MCP.
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
| `search_symbols(query, project?, target_root?, format?)`                                                                                   | Find symbol/file candidates across the active atlas. Defaults to `project="MAIN"` for target-repository coding; use `project="all"` or a concrete project key only for merge, variation, or cross-project review. `target_root` reads isolated external-target output and default `format=brief` returns a Markdown/YAML search brief.                                                                                                                                                                                                                                         |
| `check_module_integrity(module_path, target_root?, format?, max_items?)`                                                                   | Filter audit violations for a module or path. `target_root` reads isolated external-target audit evidence and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `get_surgical_context(symbol, project?, target_root?, format?)`                                                                            | Build a concise symbol briefing from atlas data. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `get_dead_code(path?, target_root?, format?, max_items?)`                                                                                  | Read dead-code findings, including public-contract protections. `target_root` reads isolated external-target output; default `format=brief` returns a bounded Markdown/YAML context brief and fails closed when the target artifact is missing.                                                                                                                                                                                                                                                                                                                                |
| `find_clones(symbol, target_root?, format?, max_items?)`                                                                                   | Inspect clone/duplication evidence for a target symbol. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `get_health_metrics(path?, target_root?, format?)`                                                                                         | Read health and risk summaries. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `get_circular_dependencies(module?, target_root?, format?, max_items?)`                                                                    | Inspect circular dependency evidence for a module or whole target. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                       |
| `get_blast_radius(symbol, target_root?, format?, max_items?)`                                                                              | Inspect expected impact of a change. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `get_state_flow(project?, max_items?, full?, target_root?, format?)`                                                                       | Follow-up-only supporting context: switch from `target_repository_default` to `target_repository_followup` after a concrete state-flow question exists. Inspect store/query state-flow artifacts. `target_root` reads isolated external-target output; default `format=brief` returns a bounded Markdown/YAML context brief. Use `full=true` or `format=json` only for machine inspection.                                                                                                                                                                                     |
| `get_hexagonal_bindings(target_root?, format?, max_items?)`                                                                                | Inspect port-adapter bindings. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `get_merge_review_queue(action?, max_items?, target_root?, format?)`                                                                       | Review human-approval-gated merge/import candidates as a bounded Markdown/YAML queue. It is advisory only and must not be used to apply file copies or imports automatically.                                                                                                                                                                                                                                                                                                                                                                                                  |
| `get_ui_architecture(component?, max_items?, full?, target_root?, format?)`                                                                | Inspect UI architecture and component surface evidence. `target_root` reads isolated external-target output; default `format=brief` returns a bounded Markdown/YAML context brief. Use `full=true` or `format=json` only for machine inspection.                                                                                                                                                                                                                                                                                                                               |
| `validate_patch(target_file, patch_content, target_root?, format?, actor_id?)`                                                             | Validate a proposed patch in memory before writing it to disk. `target_root` validates path safety and current content against the selected target repository root; when `actor_id` is supplied, it must own a current matching target-write lease; default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                              |
| `manage_target_write_lease(action, target_file, actor_id, target_root?, ttl_seconds?, format?)`                                            | Follow-up-only acquire/release protocol for cooperating agents before and after an external patch attempt. It is SQLite-backed coordination within one SAGE target-analysis deployment, not a cross-deployment filesystem lock.                                                                                                                                                                                                                                                                                                                                                |
| `get_impact_radius(target_node, target_root?, format?, depth?)`                                                                            | Read impact radius evidence for a target node. `target_root` reads isolated external-target output and default `format=brief`, `depth=2` returns a bounded Markdown/YAML directive with shown/omitted dependent counts. Use `depth=3` only when direct evidence cannot explain a failure or the human asks for broader impact; use `depth=0` only for full-graph/debug review. Missing dependency graph evidence fails closed.                                                                                                                                                 |
| `get_test_impact(target_file, target_root?, format?)`                                                                                      | Identify tests likely affected by a changed file. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `get_confidence_score(target_file, target_root?, format?)`                                                                                 | Read confidence/risk scoring for a target file. `target_root` reads isolated external-target output and default `format=brief` returns Markdown/YAML.                                                                                                                                                                                                                                                                                                                                                                                                                          |

SAGE developer/debug, release-proof, provenance, capability, pipeline,
telemetry, learning-system and workspace-maintenance tools are maintainer-only
surfaces, not part of this public target-repository coding contract.
`dag_parallel_safe` means a step may run after dependencies are satisfied;
`sequential_required` means final/broad artifact consumers should see settled
upstream outputs; `full_run_only` means the step is not a watchdog default
unless explicitly requested.

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

## Safety Guidance

- Do not edit `codemaps.discovery.json` directly.
- Prefer editing `codemaps.overrides.json` for human policy decisions.
- Do not assume a repository uses the same project names, doctrines, or module roots as this one.
- Do not execute generated mutation, merge, delete, move, or refactor actions without explicit human approval.
- Treat `output/scripts/auto_merge.*` and `output/scripts/auto_heal.*` as human-supervised operator artifacts, not normal target-repository coding context.
- `auto_merge` is a filtered merge/migration plan backed by manifest, checksum, backups and rollback; it still requires HITL review before execution.
- `auto_heal` is filtered by the generated-script policy; script-ineligible findings must appear as skipped/manual-review manifest evidence and must not be recommended to a coding agent as a default batch fix path.
- If a work queue suggests many similar fixes, inspect or validate a bounded target first; only mention generated scripts as an optional HITL-gated operator path with manifest evidence.
- Before asking for approval, use the public target-bound MCP lifecycle to create a HITL decision request with evidence and scope.
- When approval is granted or denied, use that lifecycle to record it in the HITL approval ledger before acting, then close the request.
- Keep AI answers in the Nexora response shape: verdict, evidence, confidence, risk, human_approval_required, suggested_next_action, source_artifacts.
- Record high-stakes AI answers through the public target-bound response tool when they will guide human approval or code changes.
