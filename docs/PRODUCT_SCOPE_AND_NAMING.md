# Product Scope And Naming

## Naming Strategy

- Official product name: `Nexora SAGE`.
- Formal long form: `Nexora S.A.G.E.` (`Sovereign Architectural Governance Engine`).
- Official source-tree command: `python sage.py ...`.
- Reserved future package script names: `nexora` and `nexora-sage`. Version
  1.0.0 is a source-checkout distribution and does not install console scripts.
- Internal implementation file: `codemaps.py`.
- Stable internal runtime namespace: `codemaps.*` config/schema files and `CODEMAPS_*` environment variables.

Migration principle:

- Public docs, CLI help, onboarding and MCP user-facing copy should say `Nexora SAGE`.
- Do not mass-rename stable config files such as `codemaps.config.json` before v1; those names are runtime contracts.
- Keep `codemaps.py` as internal implementation until a later CLI-module extraction; do not document it as a user command.
- Avoid cosmetic renames that break path/import/automation contracts.

## Core Scope (In)

- Workspace/project discovery and deterministic truth layer generation.
- AST/contract extraction (`atlas`, `genome`, downstream structural artifacts).
- Post-Atlas architecture blueprint proposal and human-seal readiness.
- Architecture/governance audit and violation taxonomy.
- Quality review oracle + quality gates.
- Proof obligations and proof ladder (L0-L4 envelope).
- Merge intelligence, safety gates, and sanctuary/oracle validation.
- Watchdog incremental analysis + MCP/AI context outputs.
- Release evidence, claim guard and scoped public claim enforcement.

## Out Of Scope (For Now)

- General-purpose chat assistant productization.
- Full autonomous code rewrite/refactor across host repos.
- CI/CD platform replacement.
- Non-React ecosystem expansion before React scope reaches target stability.
- All React-family runtime universality before runtime-aware v2 proof lanes exist.

## Roadmap Guardrails

- Prefer reliability over novelty:
  - determinism
  - explainability
  - reproducibility
  - contract safety
- Every new engine must provide:
  - machine-readable raw artifact
  - human-readable report
  - schema/validator coverage
  - explicit quality-gate interaction (block, warn, or advisory)
- No hidden heuristics:
  - engine behavior should be traceable via artifact evidence.
  - thresholds should be configurable via policy, not hardcoded per repo.
