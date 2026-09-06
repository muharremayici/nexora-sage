# Nexora SAGE Troubleshooting

## Node.js Not Found

Symptoms:

- TypeScript diagnostics collector reports `NODE_MISSING`.
- React frontier evidence lacks compiler diagnostics.

Fix:

```powershell
node --version
```

For release-reference parity, install Node.js 20, then rerun. Another available
major may work, but installation preflight reports it as attention until the
release matrix proves that major:

```powershell
python -m tools.engines.react_frontier_intelligence
```

## Default Profile Python Dependency Missing

Symptoms:

- MCP, Watchdog, rich terminal UI or JSON repair runtime unavailable.

These runtimes belong to the default human-and-AI profile. MCP and Watchdog are
not treated as disposable extras in the standard installation.

Fix:

```powershell
python -m pip install -r requirements.txt
python sage.py doctor --repair-optional-deps
```

## Pipeline Lock Remains

Symptoms:

- `Another Nexora SAGE pipeline run is already active`
- `.pipeline_run.lock` exists after an interrupted run.

Fix:

```powershell
python sage.py run-status --run-id "sage-run-..."
python sage.py doctor --include-validate --quick --max-seconds 180
```

Use the `run_id` printed when the command started. For an external target, add
`--target-root "C:\path\to\repository"` to the status query. `ACTIVE`,
`ACTIVE_STALE_HEARTBEAT`, `UNKNOWN` and a caller timeout do not authorize a
duplicate run or manual lock deletion. The orchestrator writes PID, run identity
and heartbeat metadata; doctor may recover a stale lock only when the process
and receipt state permit it.

## PowerShell Shows INFO as NativeCommandError

Windows PowerShell 5 can convert redirected or merged native-process stderr
lines into PowerShell error records. SAGE keeps diagnostics on stderr so MCP
JSON-RPC stdout remains uncorrupted; an INFO line labeled `NativeCommandError`
therefore does not by itself mean the SAGE command failed.

For clean-machine evidence, capture stdout and stderr separately and preserve the
native exit code. For Windows PowerShell 5, temporarily use
`$ErrorActionPreference = "Continue"` only around the native SAGE invocation,
restore the caller's original policy in a `finally` block, and record
`$LASTEXITCODE`. This prevents an expected diagnostic on stderr from aborting
capture while a real nonzero native exit remains visible. Private maintainer
transcript-generation tools are not part of the public distribution.

Treat `<COMMAND>_EXIT=0` and the command's structured terminal verdict as
authority, not PowerShell's rendering label.

If Unicode repository paths are correct in structured JSON but appear as box
drawing characters in a captured transcript, the native process bytes were
decoded with a legacy Windows console code page. Set the console, PowerShell
native-pipeline and Python stream identities to UTF-8 before invoking SAGE. Do
not rename the repository as a workaround, and do not treat a garbled human
rendering as corruption of otherwise correct structured evidence.

## Runtime Config Is Stale

Symptoms:

- Doctor says runtime truth self-heal is required.

Fix:

```powershell
python sage.py run --refresh --target-root "C:\path\to\repository" --profile daily --projects MAIN
python sage.py doctor --include-validate --quick --max-seconds 180
```

Use the explicitly initialized target root. `run --refresh` rebuilds stale
target truth without exposing the private development-only `refresh` command.

## Unicode Repository Path Fails During Initialization

Symptoms:

- Initialization writes discovery or runtime evidence, then exits non-zero
  while printing a repository path.
- Windows reports a `UnicodeEncodeError` involving a legacy console encoding.

SAGE-owned Python subprocesses are required to inherit UTF-8 mode and UTF-8
standard I/O. Do not classify a path rename as the product fix, and do not
discard already written evidence. Preserve the transcript, run the quick doctor
to identify the last authoritative state, and report the child command that
lost the UTF-8 runtime contract:

```powershell
python sage.py doctor --include-validate --quick --max-seconds 180
```

The canonical runtime sets `PYTHONUTF8=1` and
`PYTHONIOENCODING=utf-8` for SAGE-owned child processes. Target-native tools may
retain their own encoding contract; their output is decoded defensively and
must not be silently promoted into SAGE success evidence.

## Schema Parse Failure

Symptoms:

- `schemas:strict_json_parse_without_bom` fails.

Fix:

- Open the reported schema.
- Save as UTF-8 without BOM.
- Validate:

```powershell
python tools\validate_source_contracts.py
```

## Target Reports Have Empty Sections

Reports reflect the artifacts produced by the selected target and pipeline
profile. They do not silently expand a partial run into full evidence.

Fix:

```powershell
python sage.py run --target-root "C:\path\to\repository" --full --force --projects MAIN
```

Review the resulting target-bound `output/external_targets/<target-id>/reports/`
and `output/external_targets/<target-id>/.raw/` artifacts. Empty or unavailable
sections remain explicit when the target lacks the required evidence.
