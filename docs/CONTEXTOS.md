# ContextOS

ContextOS is Nexora SAGE's evidence-backed active context layer. It distills repository truth for human and AI workflows; it is not a generic RAG store or an autonomous decision maker.

## Context Contract

Every agent-facing context packet should optimize five properties:

- **Relevance:** include evidence connected to the active task, changed scope or dependency halo.
- **Sufficiency:** include enough upstream and downstream proof to avoid a locally plausible but architecturally unsafe edit.
- **Isolation:** exclude restricted, unrelated and cross-project context unless an explicit relationship justifies it.
- **Economy:** prefer the smallest evidence set that preserves the decision boundary.
- **Provenance:** identify the source artifact, confidence/proof status and reason each signal is present.

ContextOS ranks and packages evidence. Engines produce signals, the evidence layer calibrates truth, governance decides action, and humans retain sealing authority.

## Semantic Directive Principle

Repository truth is not enough by itself. An agent-facing packet must translate
that truth into the smallest useful operation surface:

- Target span: the target file, symbol or line span to inspect first
- the violated rule or active risk
- why the evidence matters
- Smallest safe fix strategy: the narrowest known repair path when one is known
- what not to touch
- which validation command or proof surface should run next

Atlas and SQLite preserve reality. ContextOS turns that reality into focused
directives that an AI coding agent can act on without reading raw artifacts.

## Long-Term Boundary

Historical cognition, external evidence adapters and runtime traces may later enrich ContextOS. Their roadmap presence does not make them current v1 capabilities.
