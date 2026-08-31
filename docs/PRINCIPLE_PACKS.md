# Principle Packs

Principle packs are SAGE's advisory engineering-wisdom layer.

They are intentionally separate from binding doctrine:

```txt
Doctrine packs   -> what this repository is allowed to do
Cage packs       -> what SAGE can automatically detect
Principle packs  -> why a safer engineering action is preferred
Capability packs -> which engines, artifacts and validators prove a claim
Directive packet -> what the AI agent should do now
```

## v1.0.0 Boundary

In product release 1.0.0, principle packs are:

- advisory only
- excluded from `architecture_doctrine.json` compilation
- non-blocking for quality gates
- useful as future directive context

They must not be treated as human-sealed repository doctrine, violation rules,
or automatic circuit-breaker triggers.

The cognitive-load pack follows the same boundary. It may help an agent avoid
complex conditionals, deep nesting, shallow abstractions or premature hard
boundaries, but it is not an enforceable V1 violation family.

The epistemic operating-loop pack follows the same boundary. It describes the
SAGE working loop:

```txt
Shared Reality -> Purpose -> Observation -> Representation -> Meaning ->
Knowledge -> Intent -> Reason -> Decision -> Action -> Result -> Feedback ->
Learning -> Capability -> System -> Shared Reality
```

It is not a new engine or release gate. It is advisory guidance that keeps
human, AI agent and SAGE aligned around the same evidence-backed reality, and
reminds contributors that feedback should return as durable lessons, validators,
source contracts, checklist items or work items.

## Files

```txt
config/principles/manifest.json
config/principles/universal/*.json
tools/core/principle_packs.py
tools/validate_principle_packs.py
```

Validate them with:

```powershell
python .\tools\validate_principle_packs.py
```

Outputs:

```txt
output/.raw/principle_pack_validation.json
output/reports/principle_pack_validation.md
```

## Design Rule

Principles explain. They do not enforce.

An agent-facing packet may later include a compact relevant principle such as:

```yaml
engineering_principles:
  - id: principle.scope.smallest_safe_patch
    directive_hint: Patch only cited target or directly related files.
```

That hint improves agent judgment without expanding the repository constitution.
