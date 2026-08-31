# Framework Adapter Boundary

Nexora SAGE keeps framework intelligence, but separates declarative knowledge from executable analysis.

Manifest-owned knowledge lives in `config/framework_capabilities.json`:

- framework package aliases
- config file names
- route markers
- directive and hook names
- evidence category mapping
- engine-to-framework capability declarations

Code-owned behavior stays in engines:

- AST traversal
- regex execution
- risk scoring
- confidence ladder assembly
- TypeScript diagnostic correlation
- artifact rendering

This is intentionally not a "no hardcoded React" policy. React/Next knowledge is the specialist surface of Nexora SAGE. The release contract is that claim-critical framework tokens are registered declaratively, while engines remain responsible for interpreting code and producing evidence.
