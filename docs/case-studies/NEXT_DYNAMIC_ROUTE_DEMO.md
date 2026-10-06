# Short static demo: route name versus doctrine layer

This small, executed **static contract demonstration** complements the
[Dub case study](NEXT_DYNAMIC_ROUTE_CASE_STUDY.md). It does not analyze a
repository, execute a React component or start a server. No Dub checkout or
target dependency installation is needed.

## Prerequisite

Use a Nexora SAGE v1.4.0 source checkout and its installed Python prerequisites
from the [Quickstart](../QUICKSTART.md). Run the commands from the directory
containing `sage.py`, not from the target repository. This demonstration calls
an existing internal Python helper; it is not a new public CLI command or a
general-purpose scanner. Custom doctrine can change classification, so the
captured result belongs to the released v1.4.0 default doctrine.

## 1. See the distinction

```powershell
python -B -c "from tools.core.layer_resolver import resolve_layer; print('app/[domain]/[key]/inspect/card.tsx ->',resolve_layer('app/[domain]/[key]/inspect/card.tsx')); print('src/domain/model.ts ->',resolve_layer('src/domain/model.ts'))"
```

Captured semantic output on the v1.4.0 public candidate:

```text
app/[domain]/[key]/inspect/card.tsx -> unknown
src/domain/model.ts -> domain/logic
```

The first path comes from the preserved Corpus sample. The second is an
original synthetic control, not a claim about a Dub source file. Neither
result establishes rule activation or permission to change code.

## 2. Check the policy boundary

Run these three existing tests; they use synthetic project/snapshot fixtures,
not a real project's approval ledger:

```powershell
python -B -m unittest tools.tests.test_engine_contracts.AuditRuleProfileContractTests.test_layer_resolution_matches_segments_not_dynamic_route_tokens tools.tests.test_engine_contracts.AuditRuleProfileContractTests.test_effective_policy_requires_exact_active_project_before_architecture_rules tools.tests.test_engine_contracts.AuditRuleProfileContractTests.test_project_policy_filter_does_not_cross_promote_architecture_findings
```

The executed command reported `Ran 3 tests` and `OK`. Startup logs and duration
may differ. The controls preserve genuine segment recognition, applicable
Next checks and Clean-specific enforcement, while rejecting an unactivated
project or a mismatched snapshot. Passing them is static contract evidence,
not runtime proof, an architecture seal or a repository-clean verdict.

## For real work

Follow the [Quickstart](../QUICKSTART.md) with an explicit target root. Then use
the [agent runbook](../AI_AGENT_HITL_RUNBOOK.md) to search and inspect a concrete
file, review current impact and applicable policy, and request focused native
validation before changing it. Do not run the maintainer release proof merely
to try SAGE or assume that this smoke reproduces a whole-repository scan.
