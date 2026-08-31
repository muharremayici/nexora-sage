from __future__ import annotations

from tools.core.adapter_registry import load_adapter_registry, summarize_adapters
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.logger import logger


def run_adapter_registry_report() -> dict:
    logger.info("Building adapter registry report...")
    registry = load_adapter_registry()
    summary = summarize_adapters(registry)
    payload = {
        "meta": {"kind": "adapter_registry", "version": "v1"},
        "summary": {key: value for key, value in summary.items() if key != "adapters"},
        "adapters": summary["adapters"],
    }
    save_json_atomic(RAW_DIR / "adapter_registry.json", payload)

    lines = [
        "# Adapter Registry",
        "",
        "Declarative ecosystem adapter registry. Adapter manifests describe supported frameworks and engine capabilities without importing executable plugin code.",
        "",
        f"- total: `{summary['total']}`",
        f"- enabled: `{summary['enabled']}`",
        f"- valid: `{summary['valid']}`",
        f"- ecosystems: `{summary['ecosystems']}`",
        f"- framework capability manifests: `{', '.join(summary.get('framework_capability_ecosystems', []))}`",
        "",
        "| Adapter | Enabled | Ecosystem | Maturity | Capabilities | Engines | Framework Manifest Coverage | Valid |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for adapter in summary["adapters"]:
        lines.append(
            f"| `{adapter['id']}` | `{adapter['enabled']}` | `{adapter['ecosystem']}` | "
            f"`{adapter['maturity']}` | `{', '.join(adapter['capabilities'])}` | "
            f"`{', '.join(adapter['engines'])}` | "
            f"`{adapter.get('framework_capability_coverage', {})}` | `{adapter['valid']}` |"
        )
    save_text_atomic(REPORTS_DIR / "adapter_registry.md", "\n".join(lines) + "\n")
    return payload


if __name__ == "__main__":
    run_adapter_registry_report()
