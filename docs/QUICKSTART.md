# Nexora SAGE Quickstart

The tested v1 Python runtime matrix is 3.11, 3.12, 3.13 and 3.14. Python
3.11 is the minimum supported version; unlisted newer versions are not
release-proven. Python is the only bootstrap prerequisite because SAGE cannot
install the interpreter needed to start itself. Install a tested Python runtime
and reopen the terminal before continuing.

## 1. Plan The Installation

The v1 installation contract is a source checkout. Wheel/PyPI packaging is not
currently supported; commands run from the repository root through
`python sage.py`.

Before dependency installation, discovery or analysis, inspect the target and
machine:

```powershell
python sage.py init --plan-only --target-root "C:\path\to\repository"
```

The default `human_and_ai_full` profile includes the CLI, Rich output, Watchdog
save-time operation and MCP agent access. These are one product profile, not
optional feature tiers. SAGE installs missing declared Python packages locally
when normal `init` runs.

Node.js is target-conditional. Canonical target preflight requires it for
JavaScript, TypeScript or React analysis when the Node-backed AST sequencer is
needed; a Python-only target does not require Node. Git is needed to acquire a
source checkout, but is not a runtime blocker after both SAGE and the target are
already present. SAGE does not silently install system runtimes. A missing
required Python or Node runtime produces `BLOCKED` with an explicit next action. Release CI currently references Node.js 20.
Another available Node major is reported as attention: it is not rejected solely
for differing, but it is not silently promoted to equivalent release-tested
coverage.

The plan is written to `output/.raw/installation_preflight.json` and
`output/reports/installation_preflight.md`. It may write this evidence inside
the SAGE installation, but it does not install packages, run discovery/analysis
or mutate the target repository.

SAGE-owned Python child processes run with UTF-8 mode and UTF-8 standard I/O,
including on Windows. Repository paths with Turkish or other Unicode characters
must not turn an otherwise completed initialization into a console-encoding
failure. If that happens, preserve the transcript and treat it as a SAGE runtime
defect rather than renaming the repository as the primary remedy.

Preflight, discovery and Oracle have separate authority. Preflight observes the
machine and target prerequisites and determines whether initialization may
proceed. Discovery produces canonical repository topology, project ownership
and runtime scope. Oracle validates selected analysis or change evidence after
those boundaries exist. They may reuse the same observations, but a preflight
exclusion is not complete unless canonical discovery carries it to every
downstream consumer.

A manifest-owned `src`, `app`, or other architecture marker remains part of
its parent project unless it declares its own project identity or workspace
edge. Internal folder shape alone does not remove the source tree from `MAIN`.

Initialization progress is emitted to the terminal/transcript and its structured
preflight artifacts. `pipeline.log` begins when the analysis pipeline begins; an
empty pipeline log during preflight is not proof that initialization is silent.

## 2. Choose The Repository Model

SAGE supports two first-class repository acquisition models:

| Model                       | Use when                                                        | Initialization                                                                             |
| --------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Embedded/default repository | One SAGE checkout is kept with one repository                   | Initialize that repository explicitly once, then use the compiled default `MAIN` workspace |
| Central/external target     | One SAGE installation analyzes several independent repositories | Supply `--target-root` for each isolated target run                                        |

Repository acquisition does not change SAGE's canonical discovery, project
ontology or analysis semantics. External targets add isolated runtime/output
namespaces; they do not define a second analysis engine.

Even in an embedded layout, the initial repository authority remains explicit.
If the SAGE checkout is directly inside the target repository, use its parent:

```powershell
python sage.py init --target-root ".."
```

For a central installation:

```powershell
python sage.py init --target-root "C:\path\to\repository"
```

Initialization defaults to setup-only: dependency preparation, discovery,
governance sync and config compilation without an analysis-freshness claim.
For the explicitly configured embedded/default repository, run a bounded
initial analysis:

```powershell
python sage.py run --profile daily --projects MAIN
```

For a central installation, preserve explicit repository authority and the
isolated output namespace on every repository-specific run:

```powershell
python sage.py run --target-root "C:\path\to\repository" --profile daily --projects MAIN
```

For one exact changed source file or a file just deleted from the current Atlas,
use the watchdog surgical pulse:

```powershell
python sage.py watch --once --target-root "C:\path\to\repository" --path "<repository-relative-source-file>"
```

The configured embedded/default repository may omit `--target-root` after its
initial explicit setup.

An exact missing path is accepted only when the previous canonical Atlas proves
that it was indexed; it is then processed as a deletion tombstone. When
`--path` names a directory, watch-once compares previous indexed membership
with the current filesystem before selecting a deterministic smoke sample.
Tombstones are selected before existing files, and the receipt reports selected
and omitted counts for both classes. Any omitted tombstone forces `UNKNOWN`;
the pulse does not claim freshness for omitted files.

`run --scope <directory>` is only a directory filter for the Scoped Host
Analyzer report. It does not bound Atlas generation, snapshots, AST parsing or
the rest of the pipeline. Exact files are rejected on that option; use the
watch-once command above instead.

The heavyweight release-deep forced bootstrap is explicit:

```powershell
python sage.py init --target-root "C:\path\to\repository" --full
```

Public product projections never treat the installation parent as implicit
repository authority.

## 3. Confirm Health

```powershell
python sage.py doctor --include-validate --quick --max-seconds 180
```

Expected result:

```text
- validators: PASS
- overall: OK
```

The default quick doctor uses the `SAGE_ON_REPOSITORY` validator profile. It
checks the installed entrypoint and bundled analyzer contracts without requiring
SAGE's own product-report evidence. The target repository's engineering verdict
still comes from the pipeline and quality gate; doctor does not replace either.

A machine remains a clean-machine target when Python, Node.js or Git are newly
installed immediately before SAGE. Record that case as
`fresh_prerequisites`, not as a `bare_os` bootstrap. Preserve the exact public
package identity, target identity, commands, stdout, stderr and native exit
codes in the evaluation transcript. Private maintainer transcript-generation
tools are not part of the public distribution. The transcript should separate
two results:

- `CLEAN_MACHINE_INSTALLATION_VERDICT` records whether the public package
  installed and executed its bounded installation proof correctly.
- `TARGET_REPOSITORY_GOVERNANCE_VERDICT` records the target's independent
  engineering result. A target `FAIL`, `INCOMPLETE_EVIDENCE`, or
  `NOT_EVALUATED` result does not turn a successful installation into a failed
  installation.

For the canonical machine-local installation proof, run:

```powershell
python sage.py install-proof --level release --skip-deps --target-root "C:\path\to\repository" --projects MAIN
```

The installation proof runs a dependency-closed daily repository analysis but
does not claim a final target governance verdict. Use a complete full or
explicit Quality Gate run when that separate verdict is required.

The `smoke` level is an internal check for an already initialized SAGE
workspace. In a fresh public package it exits before running proof steps and
points to the target-aware `release` command above instead of emitting a chain
of missing-runtime-configuration failures.

Installation validation is authority-scoped. A public package selects the
`public_target_repository` profile from `PUBLIC_DISTRIBUTION_MANIFEST.json` and
validates only shipped installation and target-repository surfaces. Canonical
source checkouts select `private_maintainer` and additionally validate clean and
public projection machinery plus SAGE release-proof integration. The selected
profile and any omitted maintainer-only checks are recorded in both JSON and
Markdown validation artifacts.

The release-level installation proof uses the same authority profile. A public
package does not invoke the intentionally unshipped private final-consistency
validator; its proof records that maintainer-only omission. Canonical maintainer
proof retains the step, and any unknown profile omission fails closed.

## 4. Connect AI IDEs

For Cursor, Codex, Antigravity or another AI-assisted IDE, add the root `SKILL.md`
to the agent's project context/rules and use the MCP preview when the IDE supports
MCP servers:

```powershell
python sage.py mcp --print-config --profile target_repository_default
```

Start ordinary coding turns in `target_repository_default`. Reconnect with
`target_repository_followup` only after a concrete supporting-context or HITL
need exists. Both profiles govern `SAGE_ON_REPOSITORY`; the public distribution
does not expose the private SAGE maintainer/self profile.

The installer does not silently copy files into IDE-specific configuration
folders. `SKILL.md` is the portable contract; IDE placement remains an explicit
operator action.

## 5. Run Common Analysis

```powershell
python sage.py run --list-steps
python sage.py run --target-root "C:\path\to\repository" --profile daily --projects MAIN
python sage.py run --target-root "C:\path\to\repository" --step qualitygates --projects MAIN
python sage.py doctor --include-validate --quick --max-seconds 180
```

## 6. Review Product Evidence

Release proof is a maintainer boundary, not a normal onboarding step. Users do
not need to rerun SAGE's complete self-release proof to analyze a repository.
See `EVIDENCE.md`, `CLAIMS_EVIDENCE_MATRIX.md` and
`V1_RELEASE_BOUNDARY.md` for the published v1 evidence and its limits.

## External Target Mode

Analyze another repository without copying Nexora SAGE into it:

```powershell
python sage.py run --target-root C:\path\to\repo --step qualitygates
```

See `docs/EXTERNAL_TARGET_RUNBOOK.md`.

## CLI Boundary

Use `python sage.py ...` for public commands. `codemaps.py` is an internal implementation file, not an onboarding surface.

## v1 Claim Boundary

For v1, Nexora SAGE should be positioned as a React/TypeScript surgical governance platform on a polyglot substrate. Runtime-only React behavior is confidence-ranked and routed toward proof; it is not overstated as statically settled. See `docs/V1_RELEASE_BOUNDARY.md`.
