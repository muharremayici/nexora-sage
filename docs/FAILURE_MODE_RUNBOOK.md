# Nexora SAGE Failure Mode Runbook

This public recovery guide is for the installed product and its explicit target,
not for private development, release approval or publication operations.

## Missing Dependencies

Use the declared installation path to inspect or prepare the product runtime:

```powershell
python sage.py init --plan-only --target-root "C:\path\to\repository"
python sage.py init --target-root "C:\path\to\repository"
python sage.py doctor --include-validate --quick --max-seconds 180
```

Missing system runtimes require explicit remediation. Do not treat setup-only
initialization as a completed target analysis.

## Interrupted Pipeline

Query the recorded invocation before considering a retry:

```powershell
python sage.py run-status --target-root "C:\path\to\repository" --run-id "sage-run-..."
```

A client timeout, stale heartbeat or unavailable status query does not prove
that the pipeline stopped. Do not delete locks or launch a duplicate solely
because the client stopped waiting.

## External Target Preflight Fails

The standalone target preflight accepts both supported target forms:

```powershell
python tools/external_target_preflight.py --target-root <repo>
python tools/external_target_preflight.py <repo>
```

`python sage.py doctor` checks the SAGE installation, not the target repository.
`doctor --target-root` redirects to target preflight or a target-scoped run
before doctor or storage work starts. Inspect root and workspace `package.json`
files when a monorepo's React/TypeScript surfaces are not recognized. A preflight
PASS is not runtime correctness or complete repository-analysis evidence.

## MCP Surface Unavailable

Preview the bounded target-repository profile and check installation health:

```powershell
python sage.py mcp --print-config --profile target_repository_default
python sage.py doctor --include-validate --quick --max-seconds 180
```

Keep target scope explicit and do not infer private release or publication
authority from MCP availability or a machine validation result.
