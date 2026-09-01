# Nexora SAGE GitHub Actions

The executable workflow lives at `.github/workflows/quality-gate.yml`. It has
two separate proof responsibilities:

- `python-runtime-compatibility` compiles every shipped root/tool Python source,
  installs product and test dependencies, starts the public CLI, and runs the
  active distribution test profile on Python 3.11, 3.12, 3.13 and 3.14 with
  Node.js 20.
- `contracts` installs the product and development dependencies on Python 3.12,
  initializes an explicit disposable target, runs quick doctor/validation, and
  proves that the target-repository test profile collects without importing
  private maintainer authority. Initialization precedes doctor because a
  successful first-run self-heal still reports the missing initial state as
  attention rather than rewriting that observation into a clean PASS.

`tools/run_distribution_tests.py` is the single test entry point. Canonical and
clean-install development deliveries run both `public_target_repository` and
`private_maintainer` classifications. A public projection contains and runs only
the machine-projected `public_target_repository` inventory declared by
`config/distribution_test_profiles.json`. Private tests remain in the canonical
and clean-install histories; they are not silently skipped or copied into the
public package.

The runtime matrix proves source compatibility for the declared tested
interpreters. It does not by itself prove every pipeline behavior on every OS.
The contracts job remains the deeper Linux integration gate; clean-machine and
platform-specific proof retain their separate evidence authority.

```yaml
jobs:
  python-runtime-compatibility:
    strategy:
      fail-fast: false
      matrix:
        python-version: ['3.11', '3.12', '3.13', '3.14']
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: python -B tools/validate_python_runtime_compatibility.py
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: python -m pip install -e ".[dev]"
      - run: npm ci --prefix tools/engines --ignore-scripts
      - run: python -B sage.py --help
      - run: python -B tools/run_distribution_tests.py

  contracts:
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: python -m pip install -e ".[dev]"
      - run: npm ci --prefix tools/engines --ignore-scripts
      - run: |
          mkdir -p "$RUNNER_TEMP/sage-ci-target/src"
          printf 'export const ciTarget = true;\n' > "$RUNNER_TEMP/sage-ci-target/src/index.ts"
      - run: python sage.py init --skip-deps --target-root "$RUNNER_TEMP/sage-ci-target"
      - run: CODEMAPS_TARGET_ROOT="$RUNNER_TEMP/sage-ci-target" python sage.py doctor --include-validate --quick --max-seconds 45
      - run: python -B tools/run_distribution_tests.py --profile public_target_repository --collect-only
```

## Fail Policy

- Any shipped Python source that cannot compile, install, start the public CLI, or
  pass the tools behavior suite on a tested runtime fails that runtime's matrix job.
- Explicit target initialization, installation/contract validation and the
  authority-scoped public test collection must pass in that order.
- A runtime matrix PASS must not be represented as Windows/macOS or full
  pipeline parity evidence.
