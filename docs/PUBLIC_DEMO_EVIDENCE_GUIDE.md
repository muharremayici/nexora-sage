# Nexora SAGE Public Repository Evidence Guide

A public demonstration must be a reproducible target-repository run, not a dump
of the private development workspace or SAGE self-release evidence.

## Reproducible Target Run

Use an explicit repository identity and bounded project scope:

```powershell
python sage.py init --target-root "C:\path\to\repository"
python sage.py run --target-root "C:\path\to\repository" --step qualitygates --projects MAIN
python sage.py mcp --print-config --profile target_repository_default
```

The run writes target-bound artifacts under
`output/external_targets/<target-id>/`. The generated pipeline log and report
metadata identify the actual paths, target identity, scope and producer state.

## What Public Evidence May Show

- discovered repository and project topology,
- evidence-backed architecture and quality findings,
- the claim boundary and unavailable evidence,
- bounded AI-agent context exposed by the public MCP profile,
- target-bound provenance and pipeline timing.

## What Public Evidence Must Not Claim

- Static evidence must not be described as production runtime tracing.
- A partial or unsupported analysis must not be promoted to complete coverage.
- Target-repository evidence must not be presented as SAGE release proof.
- Private target content, live SQLite state and unredacted raw output must not be
  published merely because SAGE generated it.
- Claims must not exceed the current claim-guard policy.

## Packaging Rule

Curated, reviewed evidence may be copied under `examples/` or `docs/` with its
target license and provenance recorded. Do not ship live `output/` folders in
the source package.

## Minimum Public Narrative

A public example should answer four questions:

1. Which exact repository identity and project scope were analyzed?
2. What did SAGE observe, infer and leave unknown?
3. What bounded information would an AI agent receive?
4. Which target-bound artifact supports each claim?
