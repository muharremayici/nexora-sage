# Built The Way It Governs

Nexora SAGE exists to make human-AI software work deterministic, bounded, evidence-backed and human-governed.

The project should therefore be built the same way it governs other repositories.

## Principle

> Human intent, AI execution, deterministic evidence, human approval.

SAGE should never ask users to trust a vague AI workflow. It should show how work was constrained, verified and recorded.

That same pattern governs SAGE's own development. The normal working mode is a
progressive HITL loop: the human challenges direction and risk, the AI agent
verifies claims against current code and artifacts, the smallest safe patch is
made, validators and manual packet review close the loop, and the next logical
step is stated explicitly.

## What This Means In The Repository

| Principle                         | Repository Surface                                                                                                                                            | Evidence                                                                                                                                                                  |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Human authority stays on top      | HITL decision requests, approval ledger, response ledger                                                                                                      | `SKILL.md`, `docs/AI_AGENT_HITL_RUNBOOK.md`, `tools/hitl_approval_ledger.py`, `tools/hitl_decision_requests.py`                                                           |
| AI agents receive bounded context | ContextOS active signals, surgical operation packet, MCP tools                                                                                                | `tools/core/contextos_mcp.py`, `tools/engines/quant_engine.py`, `tools/mcp/server.py`                                                                                     |
| Claims are evidence-backed        | Claim guard, release evidence, corpus and fixture taxonomy                                                                                                    | `docs/EVIDENCE.md`, `docs/CLAIMS_EVIDENCE_MATRIX.md`, `docs/V1_RELEASE_BOUNDARY.md`                                                                                       |
| Runtime truth is deterministic    | SQLite-first artifact store with JSON shadow exports                                                                                                          | `docs/TRUTH_MODEL.md`, `docs/ARCHITECTURE.md`, `tools/core/artifact_store.py`                                                                                             |
| Policies are explicit             | Modular doctrine packs, compiled runtime doctrine, registry and policy JSON files                                                                             | `config/doctrines/manifest.json`, `config/architecture_doctrine.json`, `config/language_registry.json`, `config/react_*_policy.json`, `docs/MODULAR_DOCTRINE_REGISTRY.md` |
| Generated state is separated      | Development output is allowed; Clean distribution and public projection exclude it, and the public projection accepts only a fingerprint-bound clean delivery | `PUBLIC_DISTRIBUTION_MANIFEST.json`, `PUBLICATION_STATUS.md`, `docs/V1_RELEASE_BOUNDARY.md`                                                                               |
| Public documentation is curated   | Current product truth is indexed while private development history stays outside the public package                                                           | `docs/DOC_STATUS_INDEX.md`, `docs/V1_RELEASE_BOUNDARY.md`                                                                                                                 |
| The product is verifiable         | Private release proof, public lineage manifest and shipped artifact contracts                                                                                 | `PUBLIC_DISTRIBUTION_MANIFEST.json`, `docs/EVIDENCE.md`, `tools/core/artifact_validator.py`                                                                               |

## Current Folder Contract

| Folder/File                            | Role                                                                       |
| -------------------------------------- | -------------------------------------------------------------------------- |
| `README.md`                            | Public product entry point: problem, value, quickstart.                    |
| `SKILL.md`                             | AI-agent operating contract.                                               |
| `config/`                              | Human and machine policy inputs.                                           |
| `tools/`                               | Engines, validators, MCP, orchestration and product generators.            |
| `docs/`                                | Current product, architecture, evidence and runbook docs.                  |
| `output/`                              | Generated development/runtime artifacts; never part of clean distribution. |
| `scratch/`, `.gemini/`, `__pycache__/` | Local development/runtime scratch; never part of clean distribution.       |

## Self-Governance Checklist

Before a public release, SAGE should answer these questions:

1. Does `README.md` explain the user pain before the architecture?
2. Does `docs/README.md` tell a new reader where to go?
3. Does `docs/EVIDENCE.md` map public claims to proof?
4. Does `SKILL.md` tell an AI agent how to use SAGE without guessing?
5. Does the private release proof pass at the exact canonical source identity?
6. Does the clean-delivery identity match the public manifest lineage?
7. Are generated/runtime files absent from the clean package?
8. Are historical audits archived away from current operating truth?
9. Are broad claims blocked by claim guard unless evidence exists?
10. Are risky mutations still routed through HITL?

## What SAGE Should Not Become

SAGE should not become:

- a collection of impressive but unverified claims
- a dumping ground for old audit reports
- an autonomous rewrite machine without human approval
- a repo-specific heuristic pile
- a dashboard-first product that hides machine-readable proof

SAGE should remain:

> a repository-owned collaboration system where human judgment, AI execution, deterministic evidence and release proof compound over time.
