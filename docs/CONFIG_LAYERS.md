# Nexora SAGE Config Layers

This document defines the three-layer configuration contract for Nexora SAGE.

## Layers

### 1. `codemaps.discovery.json`

- Machine-generated proposal layer.
- Safe to regenerate.
- Contains detected projects, aliases, plugins, architecture candidates, and seed policy proposals.
- Must not be treated as final runtime truth.

### 2. `codemaps.overrides.json`

- Human-governed layer.
- Holds canonical naming, policy corrections, thresholds, and any decisions that should survive rediscovery.
- Should be template-driven so humans edit suggestions rather than invent structure from scratch.

### 3. `codemaps.config.json`

- Compiled runtime truth.
- The only config file that engines, pipeline, watchdog, and MCP consume at runtime.
- Produced by merging defaults + discovery + overrides.

## Merge Direction

`defaults -> discovery -> overrides -> codemaps.config.json`

Rules:

- Discovery may fill missing values.
- Overrides may replace or normalize discovery proposals.
- Runtime code reads only the compiled config.

## Practical UX Goal

- Humans do not edit discovery directly.
- Humans usually edit only overrides.
- The system compiles config automatically before pipeline execution when inputs change.

## Safety Rule

The current `codemaps.config.json` remains authoritative until the compiler path is fully wired and validated.
