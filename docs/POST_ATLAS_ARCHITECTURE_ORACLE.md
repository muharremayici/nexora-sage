# Post-Atlas Architecture Oracle

Date: 2026-05-22

## Purpose

Architecture Oracle moves doctrine selection from pre-Atlas guessing toward post-Atlas evidence.

The 1.0.0 rule is conservative:

```text
Discovery scopes.
Atlas observes.
Architecture Oracle proposes.
Human seals.
Audit and Quality Gates continue to run even when a project is not sealed.
```

## Why Advisory First

The system already has a working audit and quality pipeline. Blocking those engines before a seal would make onboarding fragile, especially for external repositories and large legacy codebases.

Therefore 1.0.0 emits:

```text
output/.raw/architecture_oracle.json
output/reports/architecture_oracle.md
```

but does not rewrite `architecture_doctrine.json`.

## Evidence Model

The Oracle reads Atlas dependency geometry and computes:

```text
FSD layer coverage
FSD directional flow
FSD encapsulation ratio
Next App Router route/boundary signals
Clean/Hexagonal layer coverage
Domain-to-infrastructure leak pressure
```

It then proposes one of:

```text
FSD_STRICT
NEXTJS_APP_ROUTER
CLEAN_ARCHITECTURE
SOVEREIGN_ELITE
MODULAR_FLAT
MINIMAL
MONOREPO_PACKAGE_LIBRARY
TURBOREPO_SAAS
PLUGIN_PLATFORM
MIXED_ARCHITECTURE
```

## Seal Policy

`seal_ready=true` means:

```text
The evidence is strong enough to ask a human to seal this doctrine.
```

It does not mean:

```text
The doctrine has already been applied.
The audit should block unsealed repositories.
The AI agent can modify architecture_doctrine.json without HITL approval.
```

## MCP Surface

The Oracle is exposed through:

```text
architecture://oracle
get_architecture_oracle(project="")
```

Agents should read this proposal before interpreting architecture violations on newly onboarded external repositories.
