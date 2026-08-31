# SAGE Actor Interaction Contract

> Generated from `config/actor_interaction_contract.json`. Do not edit this document directly.
> Contract SHA-256: `91076dcb178f3cf51a27f3c765b0e2dd49e8dcb3dd39d11355cb6c4d98aa90e7`

Version: `0.3.0`
Status: `normative_draft`

## Purpose

Channel-independent interaction rules for external actors consuming SAGE engineering context, authority, evidence and validation.

Claim boundary: This contract operationalizes existing constitutional and governance authority. It does not make SAGE an agent, grant an actor authority, or expand a product capability claim.

## Constitutional Position

- Supreme authority: `docs/CONSTITUTION.md`
- Precedence: `constitution_then_sealed_doctrine_and_policy_then_this_interaction_contract_then_adapter_projection`
- Conflict rule: This contract must fail validation rather than override a higher authority source.

## Actor Model

- Principals: `human`, `organization`, `service_identity`
- Actors: `human_actor`, `tool_actor`, `pipeline_actor`, `machine_reasoning_actor`
- Adapters: `cli`, `mcp`, `api`, `ide`, `ci`, `hook`, `dashboard`

- An adapter transports an interaction; it is not automatically the acting principal.
- An actor performs or proposes work; actor identity does not grant authority.
- A principal is accountable within an explicitly granted authority scope.
- A model or conversation cannot become an engineering truth source.

## SAGE Boundary

SAGE is:

- `model_independent_deterministic_engineering_substrate`
- `bounded_context_and_evidence_provider`
- `policy_and_validation_control_plane`

SAGE is not:

- `agent`
- `agent_runtime`
- `conversation_memory`
- `universal_reasoning_engine`
- `autonomous_software_developer`
- `organizational_accountability_substitute`

Capability honesty: A capability is available only when its registry state, applicability, source evidence and freshness contract support it; otherwise SAGE must report not_available, incomplete_evidence or unknown explicitly.

## Canonical Invariants

- **MUST `engineering_reality_is_external_to_actor`:** Actor memory, plans, prompts and explanations are not authoritative engineering reality.
- **MUST `proposal_is_not_authorization`:** A proposal cannot authorize its own execution.
- **MUST `execution_is_not_validation`:** A successful write, command, build or test cannot impersonate applicable SAGE validation.
- **MUST `confidence_is_not_evidence`:** Actor confidence cannot replace deterministic evidence.
- **MUST `context_is_bounded_and_fresh`:** Context is valid only for its declared purpose, scope, snapshot, policy version and invalidation conditions.
- **MUST `failure_is_information`:** Failures and unknowns remain visible and retain their canonical semantics.
- **MUST `channel_semantics_are_equal`:** CLI, MCP, IDE, CI, hooks and future adapters must preserve scope, authority, severity and evidence meaning.
- **MUST `scope_expansion_is_explicit`:** An actor cannot silently expand mutation scope.
- **MUST_NOT `stale_context_cannot_authorize_mutation`:** A stale package cannot authorize or validate a mutation.
- **MUST_NOT `interface_hopping_cannot_bypass_governance`:** Changing adapters cannot bypass a finding, approval or scope boundary.
- **MUST `policy_change_is_separate_work`:** Policy may change only as an explicit authorized policy task, never merely to make another task pass.
- **MUST_NOT `actor_cannot_self_certify`:** A proposing or mutating actor cannot be the sole authority declaring its result valid.
- **MUST `unknown_is_explicit`:** Missing evidence must be reported as unknown, not_available or incomplete_evidence rather than inferred away.

## Finding Authority

- `engine_signal_only`: Diagnostic signal only; it cannot independently become policy or authorization. Blocking capability: `false`.
- `evidence_calibrated`: Evidence may affect a gate only through its configured actionability and quality-gate contract; evidence does not self-authorize an action. Blocking capability: `true`.
- `governance_decides`: A policy-backed governance decision may control a gate within its declared scope. Blocking capability: `true`.
- `human_approval_required`: The scoped mutation remains blocked until current explicit human authority exists. Blocking capability: `true`.

## Operation Profiles

- `inspect`: state mutation `false`; allowed mutation domains `none`; required flow `REQUESTED -> CONTEXT_RESOLVED`; terminals `ACCEPTED, INVALID_CONTEXT, INCOMPLETE_EVIDENCE, FAILED`.
- `advise`: state mutation `false`; allowed mutation domains `none`; required flow `REQUESTED -> CONTEXT_RESOLVED -> SCOPE_RESOLVED -> PROPOSED`; terminals `ACCEPTED, CLARIFICATION_REQUIRED, INVALID_CONTEXT, INCOMPLETE_EVIDENCE`.
- `propose`: state mutation `false`; allowed mutation domains `none`; required flow `REQUESTED -> CONTEXT_RESOLVED -> SCOPE_RESOLVED -> POLICY_EVALUATED -> PROPOSED`; terminals `PROPOSAL_CONFORMANT, INVALID_CONTEXT, INCOMPLETE_EVIDENCE, BLOCKED, REJECTED`.
- `mutate`: state mutation `true`; allowed mutation domains `repository_state`; required flow `REQUESTED -> CONTEXT_RESOLVED -> SCOPE_RESOLVED -> POLICY_EVALUATED -> PROPOSED -> AUTHORIZED -> EXECUTED -> VALIDATED`; terminals `ACCEPTED, PASS_WITH_ACCEPTED_RISK, BLOCKED, PARTIALLY_EXECUTED, FAILED`.
- `validate`: state mutation `false`; allowed mutation domains `none`; required flow `REQUESTED -> CONTEXT_RESOLVED -> SCOPE_RESOLVED -> VALIDATED`; terminals `PASS, PASS_WITH_ACCEPTED_RISK, ADVISE, REVIEW_REQUIRED, BLOCKED, INVALID_CONTEXT, INCOMPLETE_EVIDENCE`.
- `exception_request`: state mutation `true`; allowed mutation domains `governance_record`; required flow `REQUESTED -> SCOPE_RESOLVED -> POLICY_EVALUATED -> APPROVAL_REQUIRED`; terminals `APPROVAL_REQUIRED, REJECTED, BLOCKED, INCOMPLETE_EVIDENCE`.
- `decision_support`: state mutation `false`; allowed mutation domains `none`; required flow `REQUESTED -> CONTEXT_RESOLVED -> SCOPE_RESOLVED -> POLICY_EVALUATED`; terminals `ACCEPTED, APPROVAL_REQUIRED, INCOMPLETE_EVIDENCE`.

## Freshness

Invalidation signals: `relevant_source_change`, `repository_snapshot_change`, `policy_change`, `contract_change`, `dependency_graph_change`, `configuration_change`, `scope_expansion`, `execution_mode_change`.

Required response: `stop_mutation` -> `refresh_context` -> `re_evaluate_scope_and_policy` -> `rerun_affected_validation` -> `renew_approval_when_required`.

## Request Contract

Common fields: `request_id`, `principal_id`, `actor_id`, `actor_type`, `adapter_type`, `purpose`, `operation`, `target_scope`, `requested_mode`.

Additional `propose` fields: `repository_snapshot`, `constraints`, `expected_outcome`.
Additional `mutate` fields: `repository_snapshot`, `authority_scope`, `constraints`, `expected_outcome`.

- Purpose must be explicit when valid scope depends on intent.
- Known target scope and constraints must not be concealed.
- An adapter may downgrade to a safer mode but must never silently upgrade authority or mutation mode.

## Proposal Contract

Required fields: `proposal_id`, `request_id`, `actor_id`, `purpose`, `repository_snapshot`, `affected_scope`, `intended_changes`, `expected_effects`, `known_risks`, `required_validations`, `evidence_used`, `interaction_state`.

Non-empty fields: `proposal_id`, `request_id`, `actor_id`, `purpose`, `repository_snapshot`, `affected_scope`, `intended_changes`, `expected_effects`, `required_validations`, `evidence_used`, `interaction_state`.

Forbidden authority-key fragments: `approval`, `approved`, `authoriz`, `validation_completed`, `validated`, `accepted_result`.

Required interaction state: `PROPOSED`.

Scope rule: Each affected_scope category must be a non-empty subset of the same category in the canonical request target_scope.

Identity rule: request_id, actor_id, purpose and repository_snapshot must equal the canonical request envelope values.

Claim boundary: PROPOSAL_CONFORMANT proves only structured identity, freshness reference and declared-scope conformance. It does not prove technical correctness, authorize execution, complete validation or accept the result.

## Adapter Status

- `cli`: `partial`; `config/pipeline_execution_policy.json`, `config/cli_command_contract.json`
- `mcp`: `partial`; `config/mcp_tool_roles.json`, `config/agent_surface_contract.json`
- `ci`: `partial`; `.github/workflows/quality-gate.yml`
- `hook`: `partial`; `config/pipeline_execution_policy.json`, `tools/orchestrators/watchdog.py`
- `api`: `not_available`; no implementation evidence
- `ide`: `not_available`; no implementation evidence
- `dashboard`: `not_available`; no implementation evidence

Promotion rule: An adapter may become available only after dedicated conformance evidence proves request, authority, freshness, trace and validation semantics end to end.

## Trace And Privacy

Structured identifiers, bounded metadata and fingerprints are allowed; raw source, prompts, secrets and unrestricted model output must not be persisted by this contract.

- `operational_events`: `config/governance_trace_contract.json`
- `human_authority_decisions`: `config/hitl_governance_contract.json`

A generated narrative is not the canonical trace. Structured events and evidence identifiers are authoritative within their declared scope.

## Conformance

Minimum conformance:

- `purpose_declared`
- `operation_declared`
- `scope_explicit`
- `proposal_execution_validation_separated`
- `bounded_context_respected`
- `findings_not_bypassed`
- `authorization_respected`
- `stale_context_refreshed`
- `post_change_validation_requested`
- `significant_trace_preserved`

Full conformance additionally requires:

- `structured_requests_and_results`
- `scope_expansion_requests`
- `exception_requests`
- `snapshot_and_policy_identity`
- `safe_pause_and_resume`
- `evidence_and_validation_ids`
- `complete_change_disclosure`
- `machine_readable_failure_handling`
- `adapter_semantics_separated`
- `reproducible_interaction_replay`

## Canonical Summary

Actors may reason, navigate, propose, modify or decide according to their role and granted authority.
SAGE supplies deterministic context, constraints, evidence and validation.
Neither actor fluency nor execution success may replace engineering proof.
