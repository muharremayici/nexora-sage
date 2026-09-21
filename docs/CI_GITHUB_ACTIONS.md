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
  associated successful PR workflow receipt is present, internally intact and
  its tested Git tree equals the exact `main` tree. The reference run includes
  the same explicit target lifecycle.
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
