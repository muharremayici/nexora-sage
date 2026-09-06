# Nexora SAGE Architecture

This document is the current architecture reference for Nexora SAGE.
It describes the runtime truth model, execution flow, and artifact contracts
for repository-agnostic React/TypeScript analysis.

## 1. Product Surface

Primary operator entrypoint:

- `python sage.py <command>`

Internal implementation:

- `codemaps.py` backs the public CLI but is not a documented user command.

Top-level command visibility is exhaustive and contract-owned by
`config/cli_command_contract.json`. The public package exposes target-repository
operation plus local state administration; development and maintainer commands
remain in the private development distribution.

| Surface                                                                               | Public | System scope                         | Purpose                                                                               |
| ------------------------------------------------------------------------------------- | ------ | ------------------------------------ | ------------------------------------------------------------------------------------- |
| `init`, `install-proof`, `run`, `watch`, `doctor`, `mcp`, `purge`, `external-targets` | yes    | `SAGE_ON_REPOSITORY`                 | Target setup, analysis, live updates, health, AI access and target-output lifecycle   |
| `show-config`, `backup`, `restore`                                                    | yes    | local administration                 | Inspect or preserve the user's local SAGE runtime state; no release or seal authority |
| `target_repository_default`                                                           | yes    | `SAGE_ON_REPOSITORY`                 | Narrow primary MCP tool window                                                        |
| `target_repository_followup`                                                          | yes    | `SAGE_ON_REPOSITORY`                 | Bounded supporting-context and HITL MCP window                                        |
| unbound report/ledger CLI compatibility commands                                      | no     | private development compatibility    | Not yet target-root complete; hidden rather than overclaimed                          |
| `validate`, `setup`, `mapping`                                                        | no     | private development support          | Product development and legacy configuration surfaces                                 |
| release, self-audit, work-package, fixture, truth-sync and CI commands                | no     | `SAGE_ON_SAGE` maintainer governance | Private product proof, institutional memory and publication control                   |
| `sage_operator_debug` + `sage_self`                                                   | no     | `SAGE_ON_SAGE`                       | Exact private actor/reality pair for maintainer self-governance                       |

### 1.1 Execution Profiles And Authority

Execution identity has two independent axes:

- subject scope: `SAGE_ON_REPOSITORY` or private maintainer `SAGE_ON_SAGE`
- acquisition mode: configured `DEFAULT_WORKSPACE` or isolated `EXPLICIT_TARGET`

Default-workspace and explicit-target repository analysis use the same canonical
repository ontology, engines, applicability rules and claim semantics. The
acquisition mode may change root routing, storage namespace, freshness and
provenance; it may not change what a finding means.

The public distribution exposes `target_repository_default` for narrow primary
agent work and `target_repository_followup` for bounded supporting/HITL work.
It exposes only `SAGE_ON_REPOSITORY`. Pointing SAGE at its own source tree does
not grant product-release, lesson, debt, roadmap, seal or publication authority.
Private `SAGE_ON_SAGE` operation requires both the explicitly authorized
`sage_operator_debug` actor/tool profile and the `sage_self` reality profile;
folder names, source markers and installation-root equality are never authority.
Public users may inspect, modify, fork and test SAGE source as permitted by the
applicable license, using a target-repository profile. That is source development,
not inheritance of Nexora SAGE's private lesson/debt registries, roadmap, release
proof, human seal or publication authority.

The complete execution and acquisition contract is maintained in
`docs/EXTERNAL_TARGET_RUNBOOK.md`.

## 2. Layered Truth Model

Nexora SAGE uses four truth layers:

1. Declared truth

- Sources: `package.json`, workspace/bundler config files, tsconfig aliases.
- Generated proposal: `config/codemaps.discovery.json`.

2. Governed truth

- Human policy and overrides: `config/codemaps.overrides.json`.

3. Runtime compiled truth

- Effective runtime contract: `config/codemaps.config.json`.
- Built by `tools/config_compiler.py`.

4. Structural truth

- AST extraction via `tools/engines/ast_sequencer.cjs`.
- Combined structural payload: `atlas`, published first in SQLite `output/.raw/codemaps.db` when `use_sqlite` is enabled. Small documents remain inline in `state_payloads`; larger documents use a bounded manifest plus ordered `state_payload_parts`. The Atlas document identity and its relational project/file/symbol/dependency/source-snapshot projection commit in one transaction. `output/.raw/atlas.json` remains a compatibility/debug shadow export.

Downstream engines should consume Atlas-backed truth, not re-infer structure
from ad-hoc file heuristics.

## 2.1 Artifact Storage Model

When `config/codemaps.config.json` enables `use_sqlite`, all JSON artifacts
directly under `output/.raw/` are managed through the transparent artifact
proxy:

- reads: `load_json_file(RAW_DIR / "<artifact>.json")` resolves from SQLite
  `state_payloads`. If the SQLite row exists, the shadow JSON file is not read
  on the runtime path. Shadow JSON is read only for missing-row recovery or a
  SQLite failure path, and that fallback is honesty-telemetry visible.
- writes: `save_json_atomic(RAW_DIR / "<artifact>.json", payload)` writes the
  SQLite payload first, using bounded parts above the governed inline limit,
  then writes a full JSON shadow export for compatibility. Atlas generation also
  checkpoints producer-bound sequencer batches and completed file documents in
  non-canonical staging rows; only a complete document/relational transaction is
  visible as the current Atlas.
- bypass: `bypass_proxy=True` is reserved for shadow export, backup/restore and
  explicit disk-recovery paths.

The JSON files are still intentionally present. They are not the primary runtime
store in SQLite mode; they are human-readable exports, validator compatibility
surfaces and disaster-recovery material.

## 3. Core Execution Flow

### 3.1 Setup And Refresh Chain

Initial setup uses `sage.py init`. Subsequent public target-repository runs may
request stale-truth refresh through `sage.py run --refresh`. Both routes reuse
the shared internal refresh chain:

1. `tools/orchestrators/discovery.py`
2. `tools/governance_sync.py --apply`
3. `tools/config_compiler.py --apply`

### 3.2 Pipeline Chain

`sage.py run --full --force` calls:

- `tools/orchestrators/orchestrator.py`

Pipeline produces Atlas/Genome/core and derived artifacts under:

- `output/.raw/`
- `output/reports/`
- `output/logs/`
- `output/.snapshots/`
- `output/scripts/`

### 3.3 Validation Chain

The public validation entrypoint is `sage.py doctor --include-validate`.
Quick snapshot validation is the default; `--heavy` explicitly requests the
full heavyweight validator chain. The selected contract-owned profile decides
whether validation-critical artifacts are refreshed and which optional
ecosystem or stress validators apply. Private development distributions retain
additional direct validator orchestration, but those commands are not part of
the public CLI.

## 4. Main Runtime Components

### 4.1 Orchestrators

- `tools/orchestrators/discovery.py`
- `tools/orchestrators/orchestrator.py`
- `tools/orchestrators/watchdog.py`

### 4.2 Core Utilities

- `tools/core/config.py` (paths, output contract, runtime config load)
- `tools/core/bootstrap_env.py` (env/bootstrap entry)
- `tools/core/cache_manager.py` (incremental cache and stale detection)
- `tools/core/projects_registry.py` (project identity and aliases)
- `tools/core/vendor_bootstrap.py` (vendor path injection)

### 4.3 Engines

Representative high-signal engines:

- `generate_atlas.py`, `nuclear_processor.py`, `fractal_mapper.py`
- `audit.py` (Architectural Audit), `quality_gate.py`, `health_score.py`
- `state_flow_scanner.py`, `dead_code_detector.py`, `blast_radius_engine.py`
- `module_risk_matrix.py`, `decision_evidence.py`, `ai_context_generator.py`

## 5. Artifact Contracts

Critical contract artifacts:

- `output/.raw/codemaps.db`
- `output/.raw/atlas.json` (shadow export of SQLite `atlas` payload)
- `output/.raw/genome.json` (shadow export of SQLite `genome` payload)
- `output/.raw/surgical_discovery.json`
- `output/.raw/quality_gate.json`
- `output/.raw/react_support_matrix.json`
- `output/.raw/health_score.json`

Validation and run consumers should treat these as contract sources, then map
to human-readable reports under `output/reports/`.

## 6. Workspace and Project Semantics

Nexora SAGE supports single-project and multi-project workspaces.

- MAIN and variations are resolved through compiled runtime config.
- Project-level outputs are preferred to prevent workspace-only ambiguity.
- Aggregate workspace summaries are allowed as secondary views.

## 7. Performance Model

Performance relies on:

- cache-aware Atlas incremental behavior
- project stale detection
- selective downstream step execution
- force mode for deterministic full rebuilds

Release hardening must track run times and regressions via the universal
checklist performance ledger.

## 8. Release Hardening Reference

Public release boundary and exit evidence:

- `docs/V1_RELEASE_BOUNDARY.md`
- `docs/EVIDENCE.md`
- `docs/CLAIMS_EVIDENCE_MATRIX.md`

Validation matrix reference:

- `docs/VALIDATION_MATRIX.md`
