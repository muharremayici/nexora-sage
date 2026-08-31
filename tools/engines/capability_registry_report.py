from __future__ import annotations

from tools.core.capability_registry import load_capability_registry, summarize_capabilities
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.logger import logger


def run_capability_registry_report() -> dict:
    logger.info("Building capability registry report...")
    registry = load_capability_registry()
    summary = summarize_capabilities(registry)
    payload = {
        "meta": {"kind": "capability_registry", "version": "v1"},
        "summary": {key: value for key, value in summary.items() if key != "capabilities"},
        "capabilities": summary["capabilities"],
    }
    save_json_atomic(RAW_DIR / "capability_registry.json", payload)

    lines = [
        "# Capability Registry",
        "",
        "Machine-readable capability/plugin foundation for Nexora SAGE. Capabilities describe the platform's bounded analysis surfaces, language scope, artifacts, validators and claim boundaries.",
        "",
        f"- total: `{summary['total']}`",
        f"- valid: `{summary['valid']}`",
        f"- domains: `{summary['domains']}`",
        f"- language scopes: `{summary['language_scopes']}`",
        f"- maturities: `{summary['maturities']}`",
        "",
        "| Capability | Domain | Language Scope | Maturity | Engines | Artifacts | Validators | Valid |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for capability in summary["capabilities"]:
        lines.append(
            f"| `{capability['id']}` | `{capability['domain']}` | "
            f"`{', '.join(capability.get('language_scope', []))}` | "
            f"`{capability['maturity']}` | `{', '.join(capability.get('engines', []))}` | "
            f"`{', '.join(capability.get('artifacts', []))}` | "
            f"`{', '.join(capability.get('validators', []))}` | `{capability['valid']}` |"
        )
    save_text_atomic(REPORTS_DIR / "capability_registry.md", "\n".join(lines) + "\n")
    return payload


if __name__ == "__main__":
    run_capability_registry_report()
