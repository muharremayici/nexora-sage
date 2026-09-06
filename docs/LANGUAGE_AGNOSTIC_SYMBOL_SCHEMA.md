# Language-Agnostic Symbol Schema

Nexora SAGE v1 keeps the React/TypeScript specialization, but Atlas and Genome now carry a first shared symbol contract for future framework-independent and polyglot analysis.

## Contract

`config/language_agnostic_symbols.json` defines canonical architectural entity types such as `function`, `method`, `component`, `hook`, `class`, `struct`, `interface`, `route`, `service`, `repository`, `annotation`, `directive` and `metadata`.

Raw parser outputs stay available, but each symbol can now also carry:

- `canonical_symbol_type`
- `logic_dna`
- `logic_dna_short`
- `semantic_signature`
- `framework_tags`
- `normalization_profile`
- `semantic_depth`
- `logic_dna_kind`
- `normalization_confidence`
- `parser_kind`
- `parser_version`

## DNA Boundary

`dna` remains the raw structural/source DNA for backward compatibility.

`logic_dna` is interpreted together with `logic_dna_kind`; the hash alone is never evidence of semantic depth. In the TypeScript/React v1 profile it strips comments/whitespace, ignores `"use client"` and `"use server"` directives for the hash, and unwraps known wrappers such as `useCallback` when hashing equivalent logic. Python hashes normalized AST bodies without source locations. Java, Go and C# currently hash structural signatures only.

Framework tags are not lost. They are moved beside the logic identity as evidence, for example `next_server_boundary` or `react_callback_wrapper`.

## Claim Boundary

This is not a claim that all languages have compiler-grade semantic equivalence. It is a stable identity rail:

- React/TypeScript gets logic-first DNA for framework-shape changes.
- Python gets AST-normalized body identity without claiming mypy/pyright type semantics.
- Java/Go/C# get canonical symbol typing and explicitly low-confidence, signature-only identity.
- Parser fallback paths declare `unavailable` semantic depth instead of inheriting a stronger language default.
- Tree-sitter or compiler integrations can later plug into this schema without changing downstream Atlas/Genome consumers.

## Permanent Gate

```powershell
python tools/validate_language_agnostic_symbols.py
```

The validator proves TypeScript wrapper equivalence, Python formatting stability and logic sensitivity, canonical type preservation through the real Atlas normalization path, and explicit structural claim boundaries for Java, Go and C#.
