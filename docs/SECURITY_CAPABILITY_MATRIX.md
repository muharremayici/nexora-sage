# Nexora SAGE Security Capability Matrix

Date: 2026-06-15

This matrix separates Nexora SAGE's current security-adjacent governance capabilities from full application-security or SAST claims.

## Claim Boundary

Nexora SAGE v1 provides strong AI-agent safety, architecture-governance safety and local-first artifact control. It is not a full SAST platform and should not be positioned as a replacement for dedicated dependency-vulnerability, taint-analysis or penetration-testing tools.

## Capability Matrix

| Security Area                        | Status           | What Exists                                                                                                                               | Evidence                                                                                                                        | Boundary                                                                       |
| ------------------------------------ | ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| AI-agent patch safety                | Proven           | In-memory patch validation, impact radius, MCP governance tools, rollback-oriented task packs.                                            | `tools/engines/mcp_governance_engine.py`, `tools/mcp/server.py`, `output/.raw/ai_agent_readiness_report.json`                   | Validates architectural and structural safety; does not prove business intent. |
| Human-in-the-loop governance         | Proven           | Approval requests, approval ledger, response validation and human-supervised risky actions.                                               | `tools/hitl_approval_ledger.py`, `tools/hitl_decision_requests.py`, `docs/AI_AGENT_HITL_RUNBOOK.md`                             | Local operator discipline is still required.                                   |
| Local-first code privacy             | Proven           | Repository analysis runs locally; MCP surfaces distilled artifacts rather than uploading source to a hosted service.                      | `README.md`, `docs/NEXORA_SAGE_POSITIONING.md`                                                                                  | External AI clients may still receive whatever the user sends them.            |
| Restricted context masking           | Proven           | ContextOS and MCP packets can mask restricted, locked or secret-looking paths before surfacing context.                                   | `tools/core/contextos_mcp.py`, `tools/engines/quant_engine.py`                                                                  | Masking is a safety layer, not a full DLP product.                             |
| Artifact integrity and schema safety | Proven           | Artifact contracts, SQLite parity, clean distribution lineage and release claim guard.                                                    | `tools/engines/artifact_contract_validator.py`, `tools/validate_sqlite_artifact_parity.py`, `PUBLIC_DISTRIBUTION_MANIFEST.json` | Protects SAGE artifacts, not arbitrary application data stores.                |
| Architecture boundary safety         | Proven           | Layer, dependency, route, RSC, state/data and generated-client contract surfaces are analyzed.                                            | `tools/engines/audit.py`, `tools/engines/next_boundary_analyzer.py`, `tools/engines/state_flow_scanner.py`                      | Finds architectural risk, not every exploitable vulnerability.                 |
| Secret exposure hygiene              | Partially Proven | Public projection excludes `.env*`; ContextOS masks secret-looking files; projection contracts prevent accidental source-release leakage. | `.gitignore`, `PUBLIC_DISTRIBUTION_MANIFEST.json`, `tools/core/contextos_mcp.py`                                                | Not a full high-entropy secret scanner.                                        |
| Auth and permission logic analysis   | Partially Proven | Auth/security/API boundary signals appear in React and API surface analysis.                                                              | `output/.raw/react_runtime_intelligence.json`, external Infisical/PostHog/Sentry evidence                                       | Does not deeply prove authorization correctness.                               |
| Dependency vulnerability scanning    | Roadmap          | Optional external sensors can be bridged later.                                                                                           | `docs/ROADMAP.md`                                                                                                               | No first-class CVE advisory database integration in v1.                        |
| Runtime taint and exploit flow       | Roadmap          | Runtime tracing is explicitly out of v1 scope.                                                                                            | `docs/V1_RELEASE_BOUNDARY.md`                                                                                                   | Needs v2 runtime-aware governance.                                             |

## Public Wording

Use:

> Nexora SAGE provides local-first AI-agent safety and architecture-governance security controls.

Avoid:

> Do not claim that Nexora SAGE replaces SAST, dependency vulnerability scanners or runtime security testing.

## Recommended Pairing

For production security programs, pair Nexora SAGE with:

- Dependency vulnerability scanning
- Secret scanning
- SAST/DAST tools where required by compliance
- Code owner review and security review for sensitive modules
