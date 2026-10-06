# A real repository, one focused change question

This demo asks where to look before changing Zustand's `createStore` core.
It uses existing SAGE analysis and MCP tools, not a new scanner or synthetic
success fixture. Read the [case study](ZUSTAND_CHANGE_IMPACT_CASE_STUDY.md) for
source checks, the failed quality gate and the test-acquisition limitation.

## 1. Prepare the two separate roots

Acquire SAGE v1.4.0 and install its prerequisites using the
[Quickstart](../QUICKSTART.md). Separately acquire the Zustand checkout at
`b57db4f86ef179285da216eeb291266da82c361c`. Run SAGE commands from the directory
containing `sage.py`; the Zustand checkout is the explicit target, not the
installation directory.

The captured run reused already installed SAGE Python/TypeScript dependencies
and therefore used `--skip-deps`. Do not use that option to skip missing SAGE
prerequisites. It did not install or execute Zustand dependencies.

```powershell
$sageDemoTarget = '<absolute path to the frozen Zustand checkout>'
python -B sage.py init --skip-deps --setup-only --target-root $sageDemoTarget --projects MAIN
python -B sage.py run --target-root $sageDemoTarget --profile daily --projects MAIN --step "AI Context Generator" --ai-context
```

`--step` requests that producer's dependency closure; the command is more than
a single-file scan. Here `MAIN` resolves to `src`, with 15 indexed source files.
Setup does not itself establish fresh analysis. The actual analysis completed
with process exit **0** and governance verdict **FAIL**, not a clean verdict.

## 2. Ask the existing MCP tools

Connect this installation using the [agent runbook](../AI_AGENT_HITL_RUNBOOK.md).
Use the same absolute `target_root` in every call:

The evidence capture called these canonical server functions locally. It did
not retest an IDE client's MCP transport or grant target-edit authority.

```text
search_symbols(query="createStore", project="MAIN", target_root=TARGET, format="json")
get_impact_radius(target_node="MAIN::vanilla.ts", target_root=TARGET, format="json", depth=2)
inspect_file(target="MAIN::vanilla.ts", target_root=TARGET, format="json")
```

`TARGET` is the checkout path, not a literal argument. `MAIN::vanilla.ts` is
the indexed project-relative node; the returned public reference is
`MAIN::src/vanilla.ts`. These are tool arguments, not shell commands.

Selected fields from the **actual** impact response, with machine-local paths
and source snippets omitted:

```json
{
  "target_ref": "MAIN::src/vanilla.ts",
  "radius_depth": 2,
  "scope_completeness": "bounded",
  "dependency_graph_source": "sqlite_dependencies",
  "direct_dependents_count": 3,
  "direct_dependents": ["src/index.ts", "src/react.ts", "src/traditional.ts"]
}
```

Symbol search also returned the `createStore` definition at lines **99-100**,
plus its recorded direct import bindings in `react.ts` and `traditional.ts`.
File inspection was source-grounded and explicitly **orientation-only**, not
permission to edit. Read the core implementation and returned consumers before
proposing a change; seek the native compiler/lint/test proof appropriate to it.

## 3. Do not turn an empty result into proof

```text
get_test_impact(target_file="MAIN::vanilla.ts", target_root=TARGET, format="json")
```

The captured result had `target_grounding_status="grounded"` and
`impacted_tests=[]`. Relevant alias-bound tests exist outside the selected
`src` scope, as the case study explains. No target tests were executed or
automatically selected by this query. Missing-target control queries remained
`missing_or_unindexed`; they did not become grounded empty-test success.

The outcome is a bounded dependency/symbol review starting point, not a
complete impact set, passing suite, merge approval or runtime assurance.
