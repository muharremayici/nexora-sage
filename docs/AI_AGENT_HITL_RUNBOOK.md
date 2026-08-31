# AI Agent HITL Runbook

Nexora is the assistant to the AI agent. The AI agent is the assistant to the human. The human owns authority, approval, and final judgment.

## First Call Order

1. For live targeted work, call `get_surgical_operation_packet` or `get_active_signals`.
2. For existing technical debt cleanup, call `get_violation_work_queue`.
3. For targeted work, call `search_symbols`, then `inspect_file`, `inspect_folder`, or `inspect_symbol`.
4. Before finalizing a patch, call `get_impact_radius`, `get_test_impact`, `get_confidence_score` and `validate_patch` as needed.
   Use the default `get_impact_radius(..., depth=2)` as the normal agent
   context window. Request `depth=3` only when direct dependents and listed
   validation cannot explain the failure or the human asks for broader impact.
   Request `depth=0` only for full-graph/debug review, not ordinary code edits.
   When packets include validation command contracts, use them to distinguish
   focused validators from broad/release-style proof envelopes before deciding
   how long to wait or whether a command is appropriate for the current edit.
5. For Nexora SAGE product-readiness claims, read `EVIDENCE.md`,
   `CLAIMS_EVIDENCE_MATRIX.md` and `V1_RELEASE_BOUNDARY.md`. The public MCP
   profiles do not expose private self-release or product-readiness tools.

## Required Answer Shape

Every agent-facing answer should include:

- `verdict`
- `evidence`
- `confidence`
- `risk`
- `human_approval_required`
- `suggested_next_action`
- `source_artifacts`

Evidence must be separated from inference. If the agent is extrapolating from evidence, say so.

Use `get_agent_response_template` before composing a high-stakes answer. Use `validate_agent_response` when a downstream automation needs a machine-checkable response object.
Use `record_agent_response` to preserve high-stakes AI answers that influence human approval or code changes.
These response-contract tools shape and audit the agent answer. They do not
provide target-repository evidence and should be used after evidence is gathered.

## Human Approval Required

Ask for explicit human approval before:

- running generated mutation scripts
- applying merge/import decisions
- deleting, moving, or rewriting files
- starting large refactors or file splits
- treating browser/runtime UI findings as fully validated without manual or browser smoke proof
- analyzing an external target and presenting findings as authoritative

Before asking, switch to `target_repository_followup` and create a structured,
target-bound request with `create_hitl_decision_request`. When the human decides,
use the public follow-up MCP lifecycle to record and close that same request.
The private CLI ledger commands are not part of the public product surface.

## Safe Autonomy

The AI agent can usually proceed without extra approval for:

- reading existing artifacts
- generating brief/contract reports
- inspecting a file, folder, or symbol
- reading the published product-evidence documents
- summarizing watchdog session findings
- producing an action plan from current evidence

Missing or empty ContextOS active signals are an idle/fail-closed state. They
do not prove that the repository has no risk; choose a concrete target with
search or inspection before editing.

Impact-radius briefs are intentionally bounded. They should name the target
file/ref, direct dependents shown, transitive sample, omitted counts and
follow-up depth commands. Do not expand work merely because omitted dependents
exist; deepen the graph only when the current evidence cannot explain the
failure or the human explicitly asks for broader impact.

## MCP Surface

Primary target-repository coding tools:

- `get_surgical_operation_packet`
- `get_violation_work_queue`
- `get_active_signals`
- `search_symbols`
- `inspect_file`
- `inspect_folder`
- `inspect_symbol`
- `get_impact_radius`
- `get_test_impact`
- `get_confidence_score`
- `trace_upstream_cause`
- `validate_patch`

Default target-repository coding scope is `MAIN`. `search_symbols` and
`inspect_symbol` intentionally default to MAIN so variations are not treated as
normal edit targets. Use `project="all"` or a concrete variation project only
for merge, variation, or explicit cross-project review.

The `target_repository_followup` profile adds bounded supporting and HITL tools
after a concrete need exists, including:

- `get_operator_packet`
- `get_human_approval_gates`
- `get_hitl_decision_requests`
- `create_hitl_decision_request`
- `get_agent_response_template`
- `validate_agent_response`
- `record_agent_response`
- `inspect_folder`
- `get_dead_code`
- `get_state_flow`
- `get_circular_dependencies`
- `get_blast_radius`
- `get_ui_architecture`
- `get_merge_review_queue`
- `manage_target_write_lease`

The machine-readable profile remains authoritative over this summary. Private
SAGE self-development, release-proof, product telemetry, provenance and
learning-system tools are not callable from the public target-repository
profiles. For an MCP-less transfer, carry the bounded evidence and required
answer shape explicitly; no private handoff generator is exposed as a public
shortcut. An MCP-less handoff is not a richer substitute for the surgical
target-repository context.
The default agent path is narrow: target-repository coding turns should stay on
the primary tool surface.

## Product Positioning

Nexora is not primarily a dashboard. It is an AI-native codebase intelligence substrate with Human-in-the-Loop governance.

The intended loop is:

```text
Human gives objective
AI agent asks Nexora for evidence
Nexora returns repo intelligence and approval gates
AI agent proposes a verdict/action
Human approves, rejects, or redirects
```
