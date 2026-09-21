# Nexora SAGE v1.1.1 Clean Source Distribution

This is a clean public product projection. It is not the private development
workspace. Check `PUBLICATION_STATUS.md` for the exact publication state.

Generated workspace state, external target evidence, logs, raw reports,
machine secrets, internal work packages, private legal drafts, and local
discovery/runtime outputs are intentionally absent.

## First Run

Keep the SAGE checkout separate from the repository being analyzed and make
the target authority explicit. This is required even when the SAGE checkout
is stored directly inside the target repository; in that layout, use `..` as
the target root.

```powershell
python sage.py init --target-root "C:\path\to\repository"
python sage.py doctor --include-validate --quick --max-seconds 180
python sage.py run --target-root "C:\path\to\repository" --profile daily --projects MAIN
```

The normal `init` path is setup-only and prepares missing declared Python and
target-repository dependencies before analysis. `--skip-deps` is reserved for
controlled environments whose dependencies were independently prepared; it is
not the clean-machine default.

Replace the example path with `..` only when the parent directory is the
repository you intend to analyze. A Git-free public projection rejects an
initial targetless `init` instead of guessing repository authority.

The legacy internal module name `codemaps.py` remains a compatibility
implementation detail. The public entrypoint and product identity are
`python sage.py` and Nexora SAGE.
