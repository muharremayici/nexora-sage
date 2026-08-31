# Nexora SAGE Validation Matrix

This document defines the end-to-end validation scope for Nexora SAGE.

The goal is not only to prove that the pipeline runs, but to prove that:

- entrypoints produce the right setup state,
- core structural artifacts reflect real code,
- downstream raw artifacts stay consistent with upstream SSOT artifacts,
- markdown reports stay consistent with raw outputs,
- project-scoped semantics remain clear and enforceable,
- operational modes such as full, incremental, and single-step runs do not drift.

## Lifecycle Scope

| Layer           | Producers / Entry Points                                                                                                                                           | Primary Outputs                                                              | Validation Type                                            |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------- | ---------------------------------------------------------- |
| entry           | `sage.py init`, `sage.py run --refresh`, `tools/core/bootstrap_env.py`, `tools/orchestrators/discovery.py`, `tools/config_compiler.py`, `tools/governance_sync.py` | `codemaps.discovery.json`, `codemaps.overrides.json`, `codemaps.config.json` | contract, determinism, override precedence                 |
| structural core | `ast_sequencer.cjs`, `generate_atlas.py`, `nuclear_processor.py`                                                                                                   | `atlas.json`, `genome.json`, `surgical_discovery.json`                       | code-truth parity, contract coverage, cache parity         |
| core engines    | `audit.py`, `health_score.py`, `quality_gate.py`, `master_report_generator.py`, `decision_evidence.py`, `host_merge_intelligence.py`                               | audit, health, gate, decision, host, master outputs                          | raw consistency, report parity, project semantics          |
| derived engines | dead code, circular deps, state flow, blast radius, ai context, merge, safety, clone, temporal diff, risk matrix, closure                                          | raw JSON + markdown reports                                                  | upstream/downstream parity, selected code-truth checks     |
| operational     | `sage.py run`, `sage.py watch`, cache manager                                                                                                                      | `output/logs/pipeline.log`, incremental outputs, cache state                 | full vs incremental parity, fail-path clarity, log hygiene |
| consumers       | `tools/mcp/server.py`, `SKILL.md`, AI context, merge/safety gates                                                                                                  | assistant-facing compact views                                               | staleness, consistency, usability                          |

## Required Automated Checks

### Entry and config

1. `codemaps.discovery.json` uses `kind=codemaps.discovery`.
2. `codemaps.config.json` uses `kind=codemaps.config`.
3. compiled config variations match canonical runtime project keys.
4. overrides are reflected in compiled config.

### Structural core

1. Atlas exists and every project has a file map.
2. Atlas symbol coverage stays above the configured threshold.
3. Genome occurrences with member-bearing symbols carry the current AST contract fields.
4. Atlas and genome contract versions match `generate_atlas.AST_CONTRACT_VERSION`.
5. State-flow and blast-radius derived artifacts are non-empty when Atlas indicates eligible input.

### Audit and project semantics

1. `audit_report.summary.by_project` exists.
2. sum of `by_project` equals `summary.total`.
3. sum of `by_rule` equals `summary.total`.
4. every structured violation carries `project`, `file`, `rule`, and `detail`.
5. `quality_gate` distinguishes informational ecosystem checks from enforced project-scoped checks.

### Raw-to-report parity

1. `dead_code.json` item count matches the dead-code report headline.
2. `quality_gate.json` matches `quality_gate.md` overall status and key check rows.
3. `audit_report.json` project totals are visible in `audit_report.txt`.
4. `output/reports/MASTER_ARCHITECTURE_REPORT.md` includes project audit totals and structural contract health appendices.

### Consumer parity

1. `ai_context.json.health.overall` matches `health_score.json.overall`.
2. `ai_context.json.projects` matches current runtime project inventory.
3. decision/host/merge reports are generated from current upstream artifacts, not stale cached values.

## Manual Code-Truth Sampling

These are manual validation passes against real code and should be repeated after major core changes.

### Structural sample set

- one `MAIN` store file
- one `MAIN` service with side effects
- one variation hook with deep relative imports
- one exported interface/class pair with `extends` / `implements`
- one UI component with imported contracts and member dependencies

### Manual questions per sample

1. Does the source file really export the symbol reported by Atlas?
2. Do `imports`, `dependency_imports`, `member_imported_contracts`, and `ui_dependencies` match the code body?
3. Do side-effect markers and side-effect calls correspond to real code?
4. Does the genome occurrence preserve the symbol details from Atlas?
5. Do closure / blast / decision / safety outputs tell a story consistent with the code?

## Negative and operational tests

1. missing `atlas.json`
2. missing `genome.json`
3. stale `ai_context.json`
4. broken `quality_gate.json`
5. full run vs single-step run parity
6. cache-lift vs forced-refresh parity
7. Turkish path / UTF-8 / Windows path rendering

## Current execution strategy

### Phase 1

- inventory all producers and lifecycle stages
- add automated lifecycle validator for high-signal artifacts
- identify drift and stale outputs

### Phase 2

- expand validator coverage to more engine-specific outputs
- add selected manual truth-sampling ledger
- compare full vs incremental behavior

### Phase 3

- run universal workspace validation on a smaller external or synthetic repo
- confirm Nexora SAGE remains repository-agnostic
