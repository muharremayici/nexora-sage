# Capability Registry

Nexora SAGE capabilities are now declared in a machine-readable registry:

```txt
config/capability_registry.json
```

This registry is the 1.0.0 foundation for plugin-style growth. It does not execute plugins yet; it defines what each platform capability owns and what evidence proves it.

## What A Capability Declares

Each capability declares:

- stable `id`
- user-facing `title`
- product `domain`
- `language_scope`
- optional `framework_scope`
- related engines
- artifacts
- validators
- maturity
- active-capability `introduced_in`
- roadmap-only `target_release`
- roadmap-only `priority`, `effort`, `value`
- roadmap-only `current_state`, `roadmap_scope`, `rationale`
- claim boundary

This lets SAGE distinguish:

```txt
language expertise      -> languages/typescript-react, python_ast, go_structural
analysis capability     -> dead-code, test-impact, merge, security
governance surface      -> oracle, doctrine, audit, quality, HITL
agent surface           -> MCP, Skill, ContextOS packets
platform infrastructure -> SQLite artifact store, release proof
```

## Runtime Artifacts

Generate the registry report:

```powershell
python -m tools.engines.capability_registry_report
```

Validate the registry:

```powershell
python .\tools\validate_capability_registry.py
```

Outputs:

```txt
output/.raw/capability_registry.json
output/.raw/capability_registry_validation.json
output/reports/capability_registry.md
output/reports/capability_registry_validation.md
```

Generate the roadmap projection:

```powershell
python -m tools.engines.capability_roadmap_report
```

Outputs:

```txt
output/.raw/capability_roadmap.json
output/reports/capability_roadmap.md
```

## Project DNA Profile

The capability registry answers "what can SAGE do?" Discovery answers "what raw
workspace signals exist?" The Project DNA Profile is the bridge between those
two layers:

```powershell
python -m tools.engines.project_dna_profiler
python .\tools\validate_project_dna_profile.py
```

Outputs:

```txt
output/.raw/project_dna_profile.json
output/.raw/project_dna_profile_validation.json
output/reports/project_dna_profile.md
output/reports/project_dna_profile_validation.md
```

The DNA profile is descriptive only. It summarizes languages, frameworks,
package managers, infrastructure markers, repo shape and candidate capability
intents. It does not decide architecture and does not schedule the pipeline.
Post-Atlas Architecture Oracle remains responsible for architecture confidence.

## Capability Activation Plan

The next artifact consumes Project DNA plus the capability registry:

```powershell
python -m tools.engines.capability_activation_planner
python .\tools\validate_capability_activation_plan.py
```

Outputs:

```txt
output/.raw/capability_activation_plan.json
output/.raw/capability_activation_plan_validation.json
output/reports/capability_activation_plan.md
output/reports/capability_activation_plan_validation.md
```

The activation plan is enforced in daily and normal full profiles. Explicit,
forced and release-deep runs preserve the complete deterministic pipeline.

## Roadmap Decision Matrix

Roadmap capabilities must say more than "later." They carry a compact decision
matrix:

| Field            | Meaning                                                                                              |
| ---------------- | ---------------------------------------------------------------------------------------------------- |
| `target_release` | Intended SemVer release from the governed roadmap, summarized publicly in `docs/ROADMAP.md`.         |
| `priority`       | `P0` for near-term high leverage, `P1` for important hardening, `P2` for strategic future.           |
| `effort`         | Expected implementation cost: `low`, `medium`, `high`.                                               |
| `value`          | Expected product value: `medium`, `high`, `strategic`.                                               |
| `current_state`  | What SAGE already does today, so active capability is not accidentally sold as future or vice versa. |
| `roadmap_scope`  | What must be built before the capability is claimable.                                               |
| `rationale`      | Why the capability is positioned where it is.                                                        |

This is especially important for items like multi-agent work. SAGE is already
multi-agent compatible through MCP, `SKILL.md`, ContextOS and deterministic
artifacts. The roadmap item is narrower: bounded multi-agent orchestration with
ownership zones, locks, conflict prevention and handoff contracts.

## MCP / Agent Surface

Agents can read this registry without opening large files:

```txt
get_capability_registry(human_report?, regenerate?)
get_capability_contract(capability_id?, artifact?)
```

Use `get_capability_contract(capability_id="react_surgical_intelligence")` to see
the trusted React artifacts, validators and claim boundary. Use
`get_capability_contract(artifact="dead_code")` when starting from an artifact
and asking which capability owns that evidence.

This keeps AI agents on registry-backed evidence instead of guessing from file
names or broad README claims.

## Adapter Relationship

`config/codemaps.adapters.json` remains the ecosystem/framework adapter manifest.

Adapter capabilities must resolve to capability registry ids or aliases. This keeps framework adapters from inventing unowned capability claims.

## v1 Boundary

The registry proves plugin-readiness at the contract level. It does not claim third-party plugin loading, package marketplace support, or arbitrary external code execution.
