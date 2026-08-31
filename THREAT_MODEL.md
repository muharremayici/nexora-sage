# Nexora SAGE Threat Model

## Scope

This threat model covers the source-checkout V1 distribution, local SQLite and JSON artifacts, doctrine packs, MCP/Skill agent surfaces, external repository analysis, generated scripts, and the contract-only plugin foundation.

## Protected Assets

- Target repository source and Git history.
- Human-sealed doctrine and the HMAC-protected HITL ledger.
- Atlas, Genome, evidence, release proof, and capability claims.
- Local secrets and environment files.
- The integrity of ContextOS packets sent to AI agents.

## Trust Boundaries

| Boundary | Current posture |
|---|---|
| Target repositories | Read/analyze by default; mutation requires an explicit workflow and human approval. |
| MCP clients and AI agents | Consumers of bounded evidence, never governance authorities. |
| Doctrine changes | Human-governed; Oracle proposals are not seals. |
| SQLite and shadow JSON | SQLite-first artifact store with validation and recovery contracts. |
| Generated scripts and remediation | Validation and HITL gates apply before risky execution. |
| Plugins | Contract-only in V1; untrusted third-party code execution is disabled. |

## Principal Threats

1. An agent treats advisory evidence as a confirmed verdict or human seal.
2. A malicious or faulty plugin writes undeclared artifacts or expands product claims.
3. Path traversal or external-target confusion exposes or mutates unintended files.
4. Secrets enter ContextOS, logs, reports, or public release artifacts.
5. Stale or tampered artifacts mislead quality and release decisions.
6. Generated remediation or circuit-breaker actions destroy valid user work.

## Current Controls

- Claim Guard, evidence vocabulary, artifact schemas, and release proof.
- HMAC-SHA256 HITL ledger integrity and explicit approval gates.
- Restricted ContextOS body projection and secret-sensitive path handling.
- External-target preflight, isolated output roots, and source-relative path contracts.
- Plugin/capability/artifact declarations validated before release inclusion.
- Clean-distribution validation excludes runtime databases, output, caches, and local HITL secrets.

## Explicit Non-Claims

- V1 is not a general-purpose SAST replacement.
- V1 does not sandbox or execute untrusted third-party plugins.
- Static evidence does not prove runtime behavior; such findings remain `needs_runtime_proof`.
- SAGE does not autonomously authorize destructive source mutations.

## Reporting

Do not open a public issue for a suspected exploitable vulnerability. Use the repository's private security reporting channel once the public repository is configured. Until then, contact the maintainer privately and include the affected version, reproduction, impact, and suggested containment.
