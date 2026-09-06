# Nexora SAGE Engine / Plugin Contract Manifest

Date: 2026-06-15

This document defines the minimum contract every engine or future plugin must expose before it becomes part of the release pipeline.

## Current v1 Reality

The pipeline already records step ownership through `tools/core/pipeline_registry.py`:

- step name
- category
- dependencies
- heavy/full-only flags
- artifact reads
- artifact writes
- artifact writer conflicts

This is sufficient for v1 governance. Future plugin work should extend this contract rather than replacing it.

## Required Engine Contract

Every engine must be describable with:

| Field | Meaning |
|---|---|
| `id` | Stable machine-readable engine id. |
| `display_name` | Human-readable name. |
| `category` | Discovery, Atlas, Governance, React, Runtime, Merge, Report, Security or Polyglot. |
| `capabilities` | What the engine proves or detects. |
| `claim_boundary` | What the engine explicitly does not prove. |
| `language_scope` | `language_agnostic`, `typescript_react`, `python_ast`, `java_structural`, etc. |
| `framework_scope` | Framework-specific scope when applicable. |
| `inputs` | Required artifacts or source data. |
| `outputs` | Artifacts written by the engine. |
| `depends_on` | Pipeline steps that must run first. |
| `runtime_profile` | `surgical`, `daily`, `deep`, `full`, or `release`. |
| `failure_policy` | Whether failure blocks release, warns, or becomes advisory. |

## Plugin Lifecycle

Future plugins should follow this lifecycle:

```text
discover -> declare capabilities -> run -> write registered artifacts -> validate contracts -> expose MCP/read APIs
```

Plugins must not silently create new public claims. A plugin can add capability evidence only after:

1. Artifact contract exists.
2. Source contract or fixture exists.
3. Claim guard or capability matrix is updated.
4. Release docs state the boundary.

## v1 Guardrail

React engines may remain framework-aware and deeply specialized. Core graph, artifact, ContextOS, SQLite and MCP layers must stay language-agnostic where possible.

## Current Contract Upgrade

The v1 non-breaking upgrade is now a machine-readable capability registry:

```text
config/capability_registry.json
```

It is generated into `output/.raw/capability_registry.json`, validated by `tools/validate_capability_registry.py`, and cross-checks adapter capabilities, artifacts, validators, language scopes and claim boundaries so the source of truth does not split.

The machine-readable trust boundary is `config/plugin_trust_policy.json`. V1 accepts bundled, registered engines and declarative plugin manifests; executable third-party plugin loading remains disabled until identity, signature, isolation, resource-limit, revocation and audit requirements are implemented.
