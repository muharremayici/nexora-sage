from __future__ import annotations

from tools.core.generated_validation_commands import generated_post_validation_command


ENGINE_CONTRACT_SMOKE = generated_post_validation_command("engine_contract_smoke")


LARGE_ENGINE_REFACTOR_SEAMS = {
    "tools/engines/dead_code_detector.py": {
        "rationale": "multi-language dead-code precision engine with cache, policy, dynamic registry, and report layers",
        "safe_split_order": [
            "dead_code_cache",
            "dead_code_precision_policy",
            "dead_code_dynamic_registry",
            "dead_code_rendering",
        ],
        "required_regression_gate": ENGINE_CONTRACT_SMOKE,
    },
    "tools/engines/fractal_mapper.py": {
        "rationale": "semantic graph and architecture contract mapper with gateway inference",
        "safe_split_order": [
            "fractal_semantic_index",
            "fractal_gateway_inference",
            "fractal_contract_rendering",
        ],
        "required_regression_gate": ENGINE_CONTRACT_SMOKE,
    },
    "tools/engines/quality_gate.py": {
        "rationale": "central policy gate combining audit, doctrine, proof obligations, and quality scoring",
        "safe_split_order": [
            "quality_gate_policy_inputs",
            "quality_gate_scoring",
            "quality_gate_rendering",
        ],
        "required_regression_gate": ENGINE_CONTRACT_SMOKE,
    },
    "tools/engines/react_ecosystem_analyzer.py": {
        "rationale": "React ecosystem surgical contract engine; source scanning helpers are already isolated",
        "safe_split_order": [
            "react_source_scanner",
            "react_render_contracts",
            "react_lifecycle_contracts",
            "react_form_contracts",
            "react_merge_gates",
        ],
        "required_regression_gate": ENGINE_CONTRACT_SMOKE,
    },
}


def required_large_engine_seams() -> dict[str, dict[str, object]]:
    return LARGE_ENGINE_REFACTOR_SEAMS
