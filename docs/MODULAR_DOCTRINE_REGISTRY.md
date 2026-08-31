# Modular Doctrine Registry

Nexora SAGE keeps engine reads stable while splitting doctrine authorship into smaller policy packs.

## Runtime Contract

Engines continue to read:

```txt
config/architecture_doctrine.json
```

That file is now the compiled runtime doctrine. It is generated from:

```txt
config/doctrines/manifest.json
config/doctrines/**/*.json
```

This avoids a risky engine-wide migration while removing the long-term bottleneck of one monolithic source doctrine.

## Why This Exists

The doctrine contains unrelated policy families:

- core source and artifact rules
- governance and audit rules
- quality and health scoring rules
- language and semantic taxonomy
- React/frontend ecosystem rules
- dead-code policy
- architecture oracle policy
- merge and variation policy
- report composition rules

Keeping all of these as one authoring file makes polyglot and plugin growth harder. Modular packs let a language, framework or capability evolve without forcing every team or agent to edit the same large JSON document.

## Current Pack Groups

```txt
config/doctrines/core/
config/doctrines/governance/
config/doctrines/quality/
config/doctrines/languages/
config/doctrines/ecosystems/
config/doctrines/capabilities/
config/doctrines/discovery/
config/doctrines/oracle/
config/doctrines/reports/
config/doctrines/local/
```

`local/overrides.json` is reserved for future team or workspace overrides. It is intentionally inactive in v1 unless explicitly enabled in the manifest.

## Compilation

Run:

```powershell
python .\tools\doctrine_compiler.py
```

Validate:

```powershell
python .\tools\validate_modular_doctrine_registry.py
```

Release proof also runs the validator as a required step.

## Invariants

- Doctrine packs must declare `_meta.kind = "nexora.doctrine_pack"`.
- Packs must have unique namespaces.
- The compiler performs ordered deep merge.
- Compiled Doctrine carries a SHA-256 source fingerprint over the exact manifest and active pack bytes; runtime auto-compilation and release proof compare content rather than filesystem timestamps.
- Missing, invalid or stale required Doctrine sources fail closed instead of silently continuing with an older compiled constitution.
- `architecture_doctrine.json` must preserve all runtime doctrine keys.
- Engine code should not read individual packs directly.
- New language/framework/capability policy should enter through a pack first, then compile into the runtime doctrine.
- Audit rule profile metadata (`label`, `layer`, `default_mode`, `requires`, `rationale`) is owned only by `config/doctrines/governance/audit_rules.json`; runtime code consumes the compiled `audit_rule_profiles` contract and must not reconstruct the table in Python.
- The same pack owns `audit_rule_profile_contract`, including valid layers, default modes and requirement kinds. Unknown requirement kinds fail closed instead of being treated as satisfied.
- Repeated module-level decision rows are inspected by the hardcoded-decision inventory using the centrally configured structural-field contract. A deliberate executable table requires an explicit policy-owned exception rather than a silent local fallback.

This keeps SAGE extensible without turning every engine into a doctrine-loader variant.
