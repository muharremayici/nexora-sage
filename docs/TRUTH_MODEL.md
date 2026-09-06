# Truth Model

This document defines the current truth model for Nexora SAGE in the React/TypeScript ecosystem.

## 1. Declared Truth

Declared truth answers:

- What workspace are we in?
- What projects exist?
- What is the source root?
- Which bundler/framework/workspace signals are explicitly declared?
- Which policy choices are human-governed?

Primary sources:

- `package.json`
- workspace/config files such as `vite.config.*`, `next.config.*`, `webpack.config.*`, `turbo.json`, `pnpm-workspace.yaml`, `tsconfig.json`
- `codemaps.discovery.json`
- `codemaps.overrides.json`

Rules:

- Prefer deterministic config files before filesystem heuristics.
- Use folder/name heuristics only as fallback.
- Human policy belongs in `codemaps.overrides.json`, not in compiled runtime config.

## 2. Structural Truth

Structural truth answers:

- Which symbols exist?
- Which runtime capabilities exist?
- Which routing, state, form, API, test, and styling patterns exist?
- Which member-level side effects and contracts exist?

Primary source:

- `tools/engines/ast_sequencer.cjs`

The AST layer is responsible for extracting feature-level truth from code bodies, imports, JSX, runtime calls, and contract usage.

Examples of structural capabilities currently modeled:

- React Router
- TanStack Query
- Redux Toolkit
- Context / Provider / Suspense / Error Boundary
- React Hook Form + Zod
- API client / contract boundaries
- Testing stack
- Styling systems

## 3. Atlas SSOT

In SQLite mode, the operational SSOT is the committed Atlas generation inside
`output/.raw/codemaps.db`: `state_payloads` carries its identity and either the
small inline document or a bounded partition manifest, `state_payload_parts`
carries ordered large-document bytes, and the relational Atlas tables carry the
same transaction's query projection. `output/.raw/atlas.json` remains the full
shadow export for human inspection, validator compatibility and disaster
recovery. `atlas_staging_*` rows are resumable work-in-progress evidence and are
never current Atlas truth.

Atlas is not a third independent truth source. It is the compiled union of:

- declared truth
- structural truth

Atlas is the artifact downstream engines should trust first. Code should reach
it through `load_json_file(RAW_DIR / "atlas.json", ...)` or `load_atlas_data()`
so the transparent SQLite proxy can serve and verify the inline or partitioned
payload from the committed SQLite generation.
If a SQLite row exists, the runtime path must not read the shadow JSON export;
shadow JSON is reserved for missing-row recovery, SQLite failure recovery,
debugging and compatibility validation.

That means:

- discovery should stay light, deterministic, and workspace-oriented
- AST should stay deep, semantic, and code-oriented
- Atlas should be the merged truth consumed by downstream engines

## 4. Downstream Contract

Downstream engines should prefer Atlas-backed truth over ad-hoc heuristics.

Examples:

- `state_flow_scanner.py`
- `quality_gate.py`
- `blast_radius_engine.py`
- `decision_evidence.py`
- `ai_context_generator.py`
- `validate_react_support.py`

If a downstream engine needs a new capability, the preferred order is:

1. add it to AST truth
2. surface it through Atlas
3. consume it in downstream reports/gates

## 5. React Support Contract

For React ecosystem support, Nexora SAGE treats `react_support_matrix` as a contract artifact rather than a loose report.

Contract artifact:

- `output/.raw/react_support_matrix.json`

Human-readable report:

- `output/reports/react_support_matrix.md`

Current contract rule:

- present React capabilities should be fully detected
- partial React capabilities should be zero for the supported matrix

This is enforced in quality gates via:

- `min_react_detected_present_ratio`
- `max_react_partial_capabilities`

## 6. Layer Responsibility Summary

- `tools/orchestrators/discovery.py`
  Declared-first workspace compiler
- `codemaps.discovery.json`
  Machine proposal
- `codemaps.overrides.json`
  Human-governed policy
- `codemaps.config.json`
  Compiled runtime truth
- `ast_sequencer.cjs`
  Structural truth extractor
- `atlas.json`
  Combined SSOT
- downstream engines
  Policy, reporting, and action logic

## 7. Change Policy

When extending Nexora SAGE:

- do not patch `codemaps.config.json` by hand for policy changes
- patch `codemaps.overrides.json` or compiler inputs instead
- prefer declared truth over heuristics
- prefer AST truth over textual guessing
- prefer Atlas consumption over downstream re-interpretation

This keeps Nexora SAGE portable, explainable, and reproducible across React/TypeScript repositories.
