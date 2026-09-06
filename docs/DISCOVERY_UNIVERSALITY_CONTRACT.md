# Nexora SAGE Discovery Universality Contract

Date: 2026-06-15

Discovery is the scout layer. It must not become the final architecture judge.

The discovery validator checks all declared project roots by default. An explicit
`--projects MAIN` limits directory-presence checks to the bounded release subject,
using the shared runtime project projection. Unknown requested projects fail.
The report lists omitted presence checks; path escape and SAGE-workspace intrusion
checks still cover every declared root. This does not scan omitted projects or
claim their availability.

## Contract

Discovery may:

- detect project boundaries
- detect package/workspace managers
- detect source roots
- detect language presence from `config/language_registry.json`
- detect config files and bundlers from registry markers
- collect deterministic and heuristic evidence separately
- emit safe proposal seeds for runtime config compilation

Discovery must not:

- seal architecture doctrine
- hard-block audits
- infer final blueprint from folder names alone
- own repository-specific or language-specific host intelligence patterns in code
- override Post-Atlas Architecture Oracle decisions

## Blueprint Confidence Inputs

Architecture confidence must be derived after Atlas from:

- import/dependency direction
- public API and barrel boundary usage
- package/workspace topology
- route, entrypoint and config evidence
- cross-private edge pressure
- fan-in/fan-out and module density
- framework markers from AST/Atlas metadata

Folder names are allowed only as weak structural hints.

## Source of Truth

- Language and extension scope: `config/language_registry.json`
- Host intelligence defaults: `config/architecture_doctrine.json`
- Runtime truth: `config/codemaps.config.json`
- Blueprint proposal: `output/.raw/architecture_oracle.json`

