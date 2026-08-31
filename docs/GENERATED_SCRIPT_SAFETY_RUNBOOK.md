# Generated Script Safety Runbook

This runbook defines the operating contract for Nexora SAGE generated mutation scripts.

## Scope

Generated mutation scripts include files such as:

- `output/scripts/auto_merge.ps1`
- `output/scripts/auto_merge.sh`
- `output/scripts/auto_heal.ps1`
- `output/scripts/auto_heal.sh`

Rollback execution is handled by:

- `tools/execute_auto_merge_rollback.py`

## Non-Negotiable Rules

1. Generated mutation scripts must not run unless `CODEMAPS_ALLOW_MUTATION_SCRIPTS=1` is explicitly set.
2. Generated mutation scripts must verify their companion manifest before mutating files.
3. Generated mutation scripts must verify their own SHA256 checksum against the manifest.
4. Merge scripts must be backed by `output/scripts/auto_merge_manifest.json`.
5. Rollback execution must default to dry-run mode.
6. Rollback apply mode requires an explicit `--apply` flag.
7. Generated mutation scripts are operator/HITL artifacts, not the default target-repository coding-agent path.
8. Work-queue packets may reference generated scripts only as optional manifest-backed, human-approved operator actions.

## Agent Surface Boundary

- `get_violation_work_queue` should remain the default cleanup surface for target-repository coding agents.
- `auto_merge.*` is a filtered merge/migration plan. It can be considered only after reviewing `auto_merge_manifest.json`, backup paths, rollback behavior and HITL approval.
- `auto_heal.*` is filtered by `governance_policy.auto_heal_script_policy`; script-ineligible findings must be reported as skipped/manual-review manifest evidence. It must not be treated as a normal batch-fix command for a coding agent.
- MCP does not execute these scripts. `trigger_autonomous_fix()` is a disabled safety surface and must not be used as an approval substitute.

## Standard Verification Flow

Run the target-bound Quality Gate before considering a generated mutation
script:

```powershell
python .\sage.py run --target-root "C:\path\to\repository" --step qualitygates --projects MAIN
```

Review:

- the exact target-scoped `quality_gate.json` and human report named by the run
- `output/scripts/auto_merge_manifest.json` or the applicable generated-script
  manifest
- the target repository's own lint, compiler, policy and focused test results

Nexora SAGE's complete self-release proof validates the SAGE product. It does
not authorize a generated script against an arbitrary target repository and is
not a substitute for target-bound or target-native validation.

## Rollback Drill

Dry-run rollback:

```powershell
python .\tools\execute_auto_merge_rollback.py
```

Apply rollback only after reviewing the dry-run output:

```powershell
python .\tools\execute_auto_merge_rollback.py --apply
```

## Mutation Script Execution

PowerShell:

```powershell
$env:CODEMAPS_ALLOW_MUTATION_SCRIPTS = "1"
.\output\scripts\auto_merge.ps1
```

Bash:

```bash
CODEMAPS_ALLOW_MUTATION_SCRIPTS=1 ./output/scripts/auto_merge.sh
```

## Current Safety Position

The preferred production workflow is:

1. Run the target-bound Quality Gate and the target repository's native checks.
2. Inspect merge cockpit decisions.
3. Execute dry-run rollback.
4. Apply only the smallest approved merge batch.
5. Re-run the target-bound Quality Gate and affected native checks.

Refactor-only work is intentionally outside this runbook.
