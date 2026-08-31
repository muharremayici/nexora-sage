# Nexora SAGE Architecture Oracle Blueprint Coverage

Date: 2026-06-15

Architecture Oracle is a post-Atlas advisory engine. It observes dependency geometry and proposes a doctrine profile; it does not hard-block unsealed repositories.

Blueprint Registry v2 separates four dimensions: architectural topology, framework/runtime traits, repository shape and composition model. Runtime traits such as Next Pages Router or App/Pages hybrid do not create a new global doctrine by themselves.

`SOVEREIGN_ELITE` is the canonical profile for the fractal modular-monolith target: a modular monolith with hexagonal/clean boundaries and recursive vertical feature slices. The compatibility alias `FRACTAL_SOVEREIGN_MONOLITH` resolves to this profile; it does not create a second competing doctrine family.

## Current Blueprints

| Blueprint                  | Status                                 | Evidence                                                                                                                                       |
| -------------------------- | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `FSD_STRICT`               | Proven fixture coverage                | `tools/validate_architecture_oracle.py` FSD geometry fixture.                                                                                  |
| `NEXTJS_APP_ROUTER`        | Proven fixture coverage                | `tools/validate_architecture_oracle.py` Next App Router fixture.                                                                               |
| `MINIMAL`                  | Proven fixture coverage                | Plain starter fixture remains minimal with low confidence.                                                                                     |
| `MODULAR_FLAT`             | Proven fixture coverage                | Modular SPA geometry remains advisory and is not overclassified as FSD.                                                                        |
| `SOVEREIGN_ELITE`          | Proven fixture and externally observed | Combined FSD direction plus clean/hexagonal spine receives the more specific hybrid classification with `composition_model=fractal_recursive`. |
| `CLEAN_ARCHITECTURE`       | Proven fixture coverage                | Inward domain/application/port/adapter dependency geometry is validated.                                                                       |
| `MONOREPO_PACKAGE_LIBRARY` | Proven fixture coverage                | Package-oriented library monorepo with public API/barrel surfaces.                                                                             |
| `TURBOREPO_SAAS`           | Proven fixture coverage                | `apps/*` plus `packages/*` SaaS workspace with Next App Router signals.                                                                        |
| `PLUGIN_PLATFORM`          | Proven fixture coverage                | Plugin/provider/adapter/integration host architecture.                                                                                         |
| `MIXED_ARCHITECTURE`       | Proven fixture coverage                | Multiple strong architecture families where one global doctrine would be misleading.                                                           |

Compatibility names such as `FSD_STANDARD`, `HEXAGONAL_PURE` and `NEXTJS_APP` resolve through `config/architecture_profiles.json`; Python engines do not own separate alias tables.

## External Calibration

Durable external calibration evidence is summarized in:

- `docs/EXTERNAL_REPOSITORY_EVALUATION.md`
- `docs/REACT_VALIDATION_CORPUS_V1_EVIDENCE.md`

Important observed cases:

- `payload-main` reached `PROPOSE_SEAL` on a large CMS/admin/plugin repository.
- Plain starter surfaces remain minimal rather than inheriting host doctrine.
- Static app paths are not mistaken for Next App Router without route-convention evidence.
- `apps/<name>/app/*` monorepo App Router paths are recognized as Next/Turborepo signals.
- FSD is not counted as a mixed-architecture family when only Next's `app/` folder is present.

## Seal Policy

```text
Discovery scopes.
Atlas observes.
Architecture Oracle proposes.
Human seals.
```

`seal_ready=true` means “ask a human to review this doctrine proposal,” not “rewrite doctrine automatically.”

## v1 Boundary

The Oracle is strong enough for advisory post-Atlas blueprint proposals. It is not yet a fully autonomous architecture constitution writer.
