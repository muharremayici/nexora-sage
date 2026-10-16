# Nexora SAGE GitHub Actions

The single executable workflow remains
`.github/workflows/quality-gate.yml`. Its event tiers are owned by
`config/installation_preflight_contract.json:ci_execution_policy`; the workflow
does not maintain a second release pipeline or a second runtime-version list.

## Execution tiers

- A pull request targeting `main` runs the full distribution suite once on
  each declared Python runtime (3.11, 3.12, 3.13 and 3.14) against the exact
  `github.sha` pull-request tree. The Python 3.12 matrix member also performs
  the explicit disposable-target initialization and doctor lifecycle.
- After all four PR matrix cells pass, a dependent job publishes a receipt
  bound to the checked-out Git tree, PR number and head SHA, workflow run and
  attempt, supported runtime list, and suite command.
- A `main` push runs one Python 3.12 reference verification only when the
  associated completed, successful PR workflow belongs to the exact repository,
  workflow path, head and attempt, and has one unexpired matrix receipt in a
  complete artifact inventory. The internally intact receipt's actually tested
  commit is read independently through GitHub's Git API; both its returned
  commit identity and its actual Git tree must match the receipt and exact
  `main` tree. A second same-named artifact is ambiguous even if one is expired.
  The reference run includes the same explicit target lifecycle.
- A direct `main` push with no exact merged-pull-request association runs the
  full four-runtime matrix. Missing token or association-API failure also
  falls back to that full matrix with an explicit reason. Missing, expired,
  cancelled, bypassed, mismatched or unreadable PR receipt does the same, so an
  availability or proof problem cannot silently choose the one-runtime path.
  Malformed event data, an unsupported event, an ungoverned ref or an invalid
  source SHA fails tier resolution.

Consequently, a receipt-verified normal PR-to-main journey executes the full
suite four times on the PR tree and once on the merged main tree. The supported
runtime matrix is executed once, not once on each side of the merge. If receipt
verification cannot establish that identity, main safely runs four cells and
the journey remains eight full-suite executions. The former standalone
`--collect-only` pass was removed because the Python 3.12 full profile already
imports, collects and executes that inventory.

- Pull-request full-suite executions: `4`
- Main reference full-suite executions: `1`
- Normal PR-to-main total: `5`
- Full-matrix executions: `1`

## What each technical run proves

Every full-matrix member and the post-merge reference run performs these
commands after checking out the event's exact `github.sha`:

```text
python -B tools/validate_python_runtime_compatibility.py
python -m pip install -e ".[dev]"
npm ci --prefix tools/engines --ignore-scripts
python -B sage.py --help
python -B tools/run_distribution_tests.py
```

The Python 3.12 full-matrix member and the post-merge reference run additionally
perform the target lifecycle in this order:

```text
mkdir -p "$RUNNER_TEMP/sage-ci-target/src"
python sage.py init --skip-deps --target-root "$RUNNER_TEMP/sage-ci-target"
CODEMAPS_TARGET_ROOT="$RUNNER_TEMP/sage-ci-target" python sage.py doctor --include-validate --quick --max-seconds 45
```

Initialization precedes doctor because a successful first-run self-heal still
reports the missing initial state as attention rather than rewriting that
observation into a clean PASS.

Before public transport, the existing local installation-proof doctor step
derives the same bounded quick-validator arguments from
`ci_execution_policy.commands.target_doctor`. It uses the local interpreter and
explicit target, not the CI temporary path, and retains `--skip-release-proof`.
The current repository-quick validator set remains owned by
`config/cli_command_contract.json`; no second validator list, full-suite replay
or extra initialization is added. Failure or timeout fails installation proof
before later steps. Private maintainer doctor behavior is unchanged. This
early check does not replace fresh public CI or confer publication authority.

`tools/run_distribution_tests.py` remains the single suite entry point.
Canonical and clean-install development deliveries run both
`public_target_repository` and `private_maintainer` classifications. A public
projection contains and runs only the machine-projected
`public_target_repository` inventory declared by
`config/distribution_test_profiles.json`.

## Cache and cancellation safety

- `setup-python` caches downloaded pip artifacts using `pyproject.toml` and
  `requirements.txt` identity. Installation still runs on every job.
- `setup-node` caches the npm download cache using
  `tools/engines/package-lock.json`; `npm ci --ignore-scripts` still recreates
  the dependency tree from the lockfile.
- Only superseded runs for the same pull-request number are cancelled. Main
  pushes use their exact SHA in the concurrency key and are never cancelled by
  a later main push.

## Claim boundary

The runtime matrix proves source/runtime compatibility for the declared Python
interpreters on GitHub's Linux runner. Content-addressed cross-workflow receipt
reuse is implemented only as a prerequisite for reducing the main run; it does
not replace the bounded reference verification. Neither run proves Windows or
macOS parity, every pipeline behavior, publication authority, or independent
physical-machine installation.

The v1 matrix receipt does not capture actual installed dependency versions,
the runner image, or full interpreter/Node patch identities. Matching Git trees
therefore does not establish installed-environment equivalence and does not
authorize removing the fresh post-merge suite. The normal journey remains four
PR suites plus one reference suite; incomplete or unavailable remote commit
evidence retains the explicit full-matrix fallback.

## Actual-job environment observations

`ci_execution_policy.environment_observation` owns a separate, observational
artifact contract. Each actual matrix cell and the post-merge reference job
captures metadata immediately before the suite and after the suite and applicable
target lifecycle. The fresh matrix-receipt aggregation job cannot impersonate
these jobs. The original single-file v1 receipt and its consumers are unchanged.

The allowlisted observations bind repository, event/ref, checked-out commit tree,
workflow identity, run/attempt, job, runtime cell, actual Python version/ABI,
normalized installed Python distribution names/versions, Node ABI/version,
engine-resolved TypeScript version, runner image metadata and selected source
input hashes. Arbitrary environment variables, secrets, paths and source text
are not exported. Missing or ambiguous inventory is explicitly incomplete.

Artifacts named `nexora-sage-ci-environment-<role>-<python>-<attempt>` are retained
for 30 days, separately from `nexora-sage-pr-matrix-receipt`. They are bounded
diagnostic evidence for a subsequent comparison, not proof of successful tests,
package/binary integrity, startup-hook equivalence, event independence or absence
of intermediate changes. No consumer may treat `reuse_authorized: false` as
permission to skip execution. Incomplete observations do not claim equivalence;
invalid CI bindings or failed writes fail the observation step explicitly.

All four PR suites, the post-merge reference suite and existing full-matrix
fallback remain. This addition has not yet run in a newly published GitHub
workflow; no end-to-end speed or token saving is claimed.

The reference job now compares its completed before/after observations with the
reference-Python PR cell in shadow mode. It reuses the existing PR association,
successful matrix receipt and actual tested-commit/tree verifier, then requires
one unexpired, exact-run/attempt environment artifact in a complete inventory.
Only the two exact root observation files are read; no archive is extracted.
Their digests, phase/job/source bindings, completeness, policy/command and full
declared metadata membership are checked before comparing all four snapshots.

`MATCHING_CAPTURED_METADATA` means only that the recorded interpreter, installed
distribution versions, runner metadata, Node/TypeScript and declared source
input hashes match, with no recorded before/after drift. It does not prove full
environment, binary, startup-hook or event equivalence. `DIFFERENT_CAPTURED_METADATA`
reports bounded differing field names. Missing, corrupt, expired, ambiguous or
unverifiable evidence yields `UNAVAILABLE_EVIDENCE`, never a matching verdict.
All results retain `reuse_authorized: false`; the comparator emits no tier or
skip output. An unavailable diagnostic is not a failed runtime suite, and a
matching diagnostic is not a successful runtime-test receipt. The comparison
report accompanies the reference observations, never the legacy matrix artifact.
