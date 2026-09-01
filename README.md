# Nexora SAGE

Current version: **1.0.1**.

AI made code generation cheap.
It did not make architecture cheap.

**Nexora SAGE** is a local-first verification and governance layer for AI coding workflows. It helps AI agents work inside a closed loop: understand the repository, select focused context, respect architectural rules, validate changes, produce evidence, and keep humans in control.

## Why It Exists

AI agents can change a codebase faster than a team can understand the consequences.

Nexora SAGE gives them architectural sight:

- What changed?
- What does it affect?
- Which rules apply?
- Is the risk proven, probable, or still needs runtime proof?
- What evidence should a human review before approval?

The goal is not to replace your linter, test runner, or senior engineer. The goal is to keep AI-generated changes inside architectural reality.

## What SAGE Does

- Builds a living architecture atlas from repository structure, symbols, imports, routes, state/data flow, and governance rules.
- Specializes deeply in TypeScript/React while keeping a modular polyglot substrate for Python and broader structural language support.
- Turns watchdog and git changes into ContextOS surgical focus packets for AI agents.
- Validates architecture drift, dead code, circular dependencies, public contracts, React boundaries, merge risk, and release evidence.
- Exposes MCP tools and `SKILL.md` guidance so AI-assisted IDEs can ask for focused repository truth instead of guessing.
- Keeps risky actions under Human-in-the-Loop approval.

## v1 Claim Boundary

Nexora SAGE v1 is defensible as:

> A living architecture atlas, governance engine, and AI-agent safety layer for React/TypeScript systems, built on a modular polyglot substrate.

The current evidence-backed public claim is:

> Universal React web static architectural governance across the declared v1 static release-family taxonomy.

That boundary currently means `34/34` v1 static release families are backed by executable contracts inside an `80`-family support taxonomy, while the external corpus calibrates `22` representative behavior families against real repositories.

This is not a claim that every language has equal nanometric depth. TypeScript/React is the v1 surgical specialist surface. Python is AST-backed. Java, Go, and C# are currently structural polyglot substrate surfaces.

Parser availability is not silently promoted to parser success: Python AST fallback and TypeScript/JavaScript parse diagnostics are marked degraded, unreadable source is unavailable, and Atlas preserves current-batch coverage without turning it into a repository-wide claim.

Python framework, type-flow, ORM/migration, dependency-injection, reflection, and runtime semantics are not active v1 claims. They remain bounded roadmap candidates and require dedicated adapters, fixtures, validators, and release evidence before promotion.

This is not a claim that every runtime-only React behavior is statically decidable. Runtime-only truths remain confidence-ranked and explicitly require `needs_runtime_proof` where static evidence cannot settle them.

## Where SAGE Is Going

SAGE v1 focuses on a proven React/TypeScript governance loop: Atlas, doctrine,
ContextOS, MCP, HITL, SQLite artifacts and release proof.

The next high-leverage work stays deliberately narrow:

- hardening the new policy-backed React immutability and purity cage
- CI/CD packaging around existing release proof
- documentation-to-evidence governance

Larger platform work such as provider topology, privacy/performance/resilience
cages, AST daemonization, runtime tracing, deeper polyglot drivers and bounded
multi-agent orchestration is tracked in the registry-backed roadmap.

See [Roadmap](docs/ROADMAP.md) and [Capability Registry](docs/CAPABILITY_REGISTRY.md).

## Quickstart

Python 3.11, 3.12, 3.13 and 3.14 are the tested v1 runtime matrix. Python
3.11 is the minimum. Python is the bootstrap prerequisite: because SAGE itself
runs on Python, it cannot install a missing Python runtime before it starts.
Install one tested Python version, reopen the terminal, then let SAGE inspect the
remaining machine and repository requirements.

V1 is distributed as a GitHub source checkout. Run `python sage.py` from the
repository root; wheel/PyPI installation is not a current release claim.

Inspect the machine and target without installing packages or running analysis:

```powershell
python sage.py init --plan-only --target-root "C:\path\to\repository"
```

Then initialize the target:

```powershell
python sage.py init --target-root "C:\path\to\repository"
```

The default `human_and_ai_full` profile keeps CLI, Rich output, Watchdog and MCP
available together. SAGE installs missing Python packages into its own runtime
through the declared dependency manifest. Node.js is required only when
canonical target preflight observes JavaScript, TypeScript or React and the
Node-backed AST sequencer is needed; Python-only targets do not require Node.
SAGE never silently installs system runtimes or mutates the target repository
during dependency setup. Missing Python/Node system runtimes produce a blocking,
explicit remediation instead. Release CI currently references Node.js 20. A
different available Node major is reported as attention rather than rejected or
silently treated as equivalent release coverage.

Initialization defaults to setup-only: dependency preparation, discovery,
governance synchronization and config compilation without an analysis-freshness
claim. Use `--full` only for the explicit heavyweight first analysis. The SAGE
checkout is the installation root, not the repository under analysis; public
product projections require the target explicitly. SAGE prints the selected
cost tier, scope, project count and local duration evidence before an executing
init mode starts.

Check workspace health:

```powershell
python sage.py doctor --include-validate --quick --max-seconds 180
```

This quick doctor is repository-scoped: it validates the installed SAGE
entrypoint and bundled analyzer contracts, while the target pipeline and quality
gate remain the authority for repository findings.

Connect AI-assisted IDEs by adding the root `SKILL.md` to the agent's project context/rules. When the IDE supports MCP servers, preview the local MCP config:

```powershell
python sage.py mcp --print-config --profile target_repository_default
```

The public agent surface has two bounded profiles: start with
`target_repository_default`, then use `target_repository_followup` only for a
concrete supporting-context or HITL need. Both are `SAGE_ON_REPOSITORY`.
Analyzing the SAGE source tree remains ordinary repository analysis and cannot
inherit private SAGE release, lesson, debt, roadmap, seal or publication
authority. See [External Target Runbook](docs/EXTERNAL_TARGET_RUNBOOK.md).

## Common Commands

```powershell
python sage.py run --list-steps
python sage.py run --target-root "C:\path\to\repository" --profile daily --projects MAIN
python sage.py run --target-root "C:\path\to\repository" --step qualitygates --projects MAIN
python sage.py doctor --include-validate --quick --max-seconds 180
python sage.py watch --once --target-root "C:\path\to\repository"
```

After an embedded/default repository has been explicitly initialized, its
compiled workspace may use the corresponding targetless forms. A central SAGE
installation should keep `--target-root` on every repository-specific run so
target authority and artifact isolation remain visible.

The complete frozen-source release proof is a maintainer-only release gate, not
a normal user command. Public operation uses the scoped `sage.py` surfaces
above.

`codemaps.py` is an internal implementation file. Public docs and onboarding use `sage.py`.

## Outputs

- `output/.raw/codemaps.db`: SQLite-backed artifact store and primary runtime SSoT when `use_sqlite` is enabled.
- `output/.raw/*.json`: compatibility/debug shadow exports for validators, agents, and recovery.
- `output/reports/`: human-readable reports.
- `output/logs/pipeline.log`: pipeline execution log.

## How To Read The Docs

Start here:

- [Quickstart](docs/QUICKSTART.md)
- [Documentation Map](docs/README.md)
- [Built The Way It Governs](docs/BUILT_THE_WAY_IT_GOVERNS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Evidence](docs/EVIDENCE.md)
- [Claim Evidence Matrix](docs/CLAIMS_EVIDENCE_MATRIX.md)
- [React Validation Evidence](docs/REACT_VALIDATION_CORPUS_V1_EVIDENCE.md)
- [Security Capability Matrix](docs/SECURITY_CAPABILITY_MATRIX.md)
- [V1 Release Boundary](docs/V1_RELEASE_BOUNDARY.md)

## Extending SAGE Safely

SAGE is meant to grow through contracts, not ad-hoc scripts. For local
modifications or forks permitted by the applicable license, and for any future
external contribution intake, start with these sources before adding a doctrine,
policy, principle, cage, capability, engine, plugin manifest, or agent-facing
MCP surface:

- `config/governance_registry.json`
- `config/contributor_extension_contract.json`
- `CONTRIBUTING.md`

Those files define the owning registry or manifest, required validator,
release-proof step, claim boundary, and Progressive HITL expectation for each
extension surface. External copyrighted contributions remain closed for v1 as
stated in `CONTRIBUTING.md`. Every permitted local extension, and any future
accepted contribution, must preserve the separation: doctrine binds, principles
guide, cages detect, capabilities scope, and ContextOS directives tell agents
what to do now.

## Positioning

Nexora SAGE is not a Biome, ESLint, Knip, Madge, or SonarQube replacement. Those tools can be useful optional sensors.

SAGE is different because it closes the loop around AI coding:

1. Discover repository truth.
2. Build architectural evidence.
3. Distill focused context for agents.
4. Validate changes against rules and contracts.
5. Produce proof obligations and release evidence.
6. Require human approval for risky actions.
7. Preserve the lessons in repo-owned artifacts.

In short:

> AI agents generate. SAGE verifies. ContextOS focuses. Humans decide.

SAGE is also built by the same principles it gives to AI agents: scoped intent, deterministic artifacts, evidence-backed claims, clean release boundaries and human approval for risky actions.

## License

This repository is offered under either PolyForm Noncommercial 1.0.0 or PolyForm Free Trial 1.0.0 when the selected grant covers the intended use. The grants are alternatives, not cumulative permissions. Nexora SAGE 1.0.1 preserves the v1.0.0 grant boundary and offers no permission, contribution-rights agreement, or automatic future-license transition beyond the two shipped grants. `LICENSE` and the shipped grant texts are authoritative; `LICENSING.md` provides a human-readable guide.

`NOTICE.md` states the SAGE-specific release-status, evidence, certification and
human-review boundaries. In particular, a SAGE `PASS` or release-proof is
bounded engineering evidence, not a certification that a target repository is
safe, secure, compliant, deployable or fit for a particular purpose.
