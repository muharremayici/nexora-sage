# Nexora SAGE Polyglot Capability Matrix

Nexora SAGE should be positioned as a polyglot architecture governance substrate. The honest technical boundary is that TypeScript/React remains the deepest specialist surface, Python is strong AST-backed support, and Java/Go/C# are currently structural symbol/import extraction surfaces.

| Language | Claim Level | What Is Proven | What Is Not Claimed |
|---|---|---|---|
| TypeScript | `deep_specialist` | AST sequencing, import/dependency resolution, TypeScript diagnostics, React frontier/runtime/compiler readiness and framework contracts. | Full runtime proof for every dynamic path. |
| JavaScript | `deep_specialist` | AST sequencing, import/dependency resolution and React/framework-aware structural analysis. | TypeScript-only typecheck diagnostics. |
| Python | `ast_strong` | Python AST sequencing, import extraction and normalized Atlas symbol integration. | mypy-grade type semantics; runtime import execution; Django/FastAPI framework semantics; dependency-injection resolution; ORM/migration compatibility; interprocedural data flow; runtime middleware/route resolution; reflection, metaclass and monkey-patch behavior. |
| Java | `structural` | Package/class/method/import structural extraction and normalized Atlas integration. | javac-grade semantics or Maven/Gradle dependency resolution. |
| Go | `structural` | Package/function/import structural extraction and normalized Atlas integration. | go/types semantics or go list module resolution. |
| C# | `structural` | Namespace/class/method/using structural extraction and normalized Atlas integration. | Roslyn semantics or dotnet build diagnostics. |

## Release Guardrail

The release contract is intentionally conservative:

- Do call Nexora SAGE a polyglot architecture governance substrate.
- Do state that TypeScript/React is the deepest specialist ecosystem.
- Do state that Python support is AST-backed.
- Do state that Java/Go/C# support is structural until compiler-grade adapters exist.
- Do not claim javac, go/types or Roslyn-grade semantics before dedicated adapters and proof artifacts exist.

Python framework and runtime semantics are roadmap candidates, not active claims. Candidate directions include framework-semantic adapters, type-semantic integration, ORM/migration contracts and runtime-trace integration. Candidate status records intended investigation, not a delivery promise or active capability.

Parser capability and parser execution evidence are distinct. Python is AST-backed when a file reports `observed/python_ast`; syntax fallback is explicitly `degraded/regex_fallback`. TypeScript/JavaScript uses the compiler API, but parse diagnostics lower the file to `degraded/partial_ast` rather than silently claiming complete AST evidence. An unreadable source is `unavailable`, never a confirmed empty symbol set. Atlas stores these states for the current materialization batch and does not promote incremental or cache-lift evidence into a repository-wide parser claim. Java, Go and C# retain their narrower `signature_only` structural boundary under the same unavailable-source rule.

Machine-readable source of truth: `config/polyglot_capabilities.json`.
