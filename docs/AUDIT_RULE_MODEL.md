# Audit Rule Model

Nexora SAGE treats audit rules as a layered system rather than a single flat policy set.

## Layers

### Universal

Rules that are broadly portable across modern TypeScript/JavaScript repositories.

Examples:

- `relative_imports_no_alias`
- `loc_limits_hook`
- `loc_limits_component`
- `loc_limits_service`

These default to `enforced`.

`relative_imports_no_alias` means **Canonical Alias Boundary Bypass**. It applies only to JavaScript/TypeScript workspace scopes that actually declare a canonical path alias. It preserves relative imports inside a declared module boundary, excludes package subpaths and declared stylesheet side effects, and requires the canonical alias when a local path crosses that safe boundary. The rule does not mean that every relative import is invalid, and it is not limited to a fixed `../` depth.

### Architectural

Rules that become meaningful only when the repository follows a known architecture style.

Examples:

- `deep_imports`
- `own_api_imports`
- `relative_module_escapes`
- `fsd_shared_features`
- `domain_ui_leaks`
- `domain_platform_leaks`
- `impure_stores`

These usually default to `advisory` and become stronger when their architectural assumptions are present.

### Doctrine

Rules that depend on repository-specific governance, platform choices, or team doctrine.

Examples:

- `zustand_no_selector`
- `banned_i18n`

These usually default to `disabled` or `advisory` unless the repo explicitly enables them.

## Runtime Modes

Each rule is evaluated into one of these modes for the current workspace:

- `enforced`
- `advisory`
- `disabled`

The mode depends on:

- current architecture type
- detected plugin set
- project count
- explicit audit rule enablement in `codemaps.config.json`

## Why this matters

This prevents Nexora SAGE from treating every repository as if it were the same codebase.

- Universal rules remain strong across repos.
- Architectural rules stay honest about assumptions.
- Doctrine rules do not become false global truth.
