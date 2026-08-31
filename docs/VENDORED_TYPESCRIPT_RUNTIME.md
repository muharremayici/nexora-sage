# Controlled TypeScript Runtime

## Purpose

Nexora SAGE uses a local Node/TypeScript runtime for AST-aware and compiler-diagnostic support. This runtime is a controlled dependency for analysis tooling; it is not product source and must not be treated as a first-party implementation surface.

The public GitHub source checkout does not include `node_modules`. Initialization
installs the lockfile-controlled runtime into the local environment; release
proof validates that installed copy and its original license/notice files.

## Current Dependency

- Package manifest: `tools/engines/package.json`
- Lockfile: `tools/engines/package-lock.json`
- Declared TypeScript version: `5.9.3`
- Locked TypeScript version: `5.9.3`
- TypeScript license: `Apache-2.0`
- Lockfile SHA-256: `99611E4256C514D7A2A07C644B53B7FAD30E0E4B25D7E0282BB5EA9A7DF0DCA6`
- Runtime license file SHA-256: `A7D00BFD54525BC694B6E32F64C7EBCF5E6B7AE3657BE5CC12767BCE74654A47`
- Runtime third-party notice SHA-256: `1AF3C68039C57E539422DA82A4FAADA506CE6D0EA6F90E0B699D02DBCDB7A90C`
- Canonical contract: `config/third_party_distribution_contract.json`

## Consumers

- `tools/engines/ts_diagnostics_collector.cjs`
- React frontier diagnostics in `tools/engines/react_frontier_intelligence.py`
- Any future AST/compiler collector that imports TypeScript from `tools/engines/node_modules`

## Upgrade Process

1. Update `tools/engines/package.json`.
2. Regenerate `tools/engines/package-lock.json` with npm in `tools/engines`.
3. Keep the manifest version exact, run `npm ci --ignore-scripts`, and record the new TypeScript version, license and lockfile SHA-256 in this document.
4. Run the smoke commands below.
5. Review changes to React frontier diagnostic counts before accepting signal drift.

## Smoke Commands

```powershell
node .\tools\engines\ts_diagnostics_collector.cjs --config .\config\codemaps.config.json --out .\output\.raw\ts_diagnostics.json --maxDiagnostics 50 --maxFiles 80 --semantic false
python -m tools.engines.react_frontier_intelligence
python .\tools\validate_react_fixtures.py
```

## Governance

- Owner: Nexora SAGE governance
- Provenance contract: `config-provenance-v1`
- Upgrade review must include lockfile hash, license check, diagnostic-count diff and smoke command results.
