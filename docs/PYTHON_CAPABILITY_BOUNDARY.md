# Nexora SAGE Python Capability Boundary

Date: 2026-06-15

Python is the second most important specialization path after TypeScript/React.

## Current v1 Claim

Python support is AST-backed:

- Python source discovery
- AST symbol extraction
- import extraction
- normalized Atlas integration
- language-agnostic symbol identity

## Not Claimed In v1

Nexora SAGE v1 does not claim:

- mypy-grade type checking
- runtime import execution
- FastAPI/Django semantic completeness
- Celery/task runtime tracing
- SQL/ORM taint analysis

## Recommended 1.5.0 / 2.0.0 Expansion

| Area | Why It Matters |
|---|---|
| FastAPI route and dependency graph | Common AI-era backend surface paired with React. |
| Django app/model/view graph | Important mature Python web ecosystem. |
| Pydantic schema/validation flow | Links API input contracts to route safety. |
| Background job topology | Celery/RQ/async jobs affect blast radius. |
| Python test-impact matching | Strengthens AI-agent safe edits in backend files. |
| Type-hint and Protocol surfaces | Protects public contracts and library APIs. |

## Release Wording

Use:

> Python support is AST-backed and integrated into the same architecture governance substrate.

Avoid:

> Python has the same nanometric depth as the React/TypeScript specialist layer.

