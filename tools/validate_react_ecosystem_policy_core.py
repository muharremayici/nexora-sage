from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


ECOSYSTEM_ENGINE = CODE_MAPS_DIR / "tools" / "engines" / "react_ecosystem_analyzer.py"
STATE_DATA_ENGINE = CODE_MAPS_DIR / "tools" / "engines" / "state_data_graph_analyzer.py"
ECOSYSTEM_POLICY = CONFIG_DIR / "react_ecosystem_policy.json"
STATE_DATA_POLICY = CONFIG_DIR / "state_data_graph_policy.json"

def _check(name: str, passed: bool, details: str, evidence: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "details": details,
        "evidence": evidence,
    }


def _missing(tokens: list[str], text: str) -> list[str]:
    return [token for token in tokens if token not in text]


def _policy_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _provider_pattern_errors(data_providers: Any) -> list[dict[str, Any]]:
    if not isinstance(data_providers, dict):
        return [{"provider": "<data_cache_providers>", "pattern": None, "error": "must be an object"}]
    errors: list[dict[str, Any]] = []
    for provider_name, provider in data_providers.items():
        if not isinstance(provider, dict):
            errors.append({"provider": str(provider_name), "pattern": None, "error": "provider must be an object"})
            continue
        patterns = provider.get("patterns", [])
        if not isinstance(patterns, list):
            errors.append({"provider": str(provider_name), "pattern": None, "error": "patterns must be a list"})
            continue
        for raw in patterns:
            try:
                re.compile(str(raw), re.MULTILINE)
            except re.error as exc:
                errors.append({"provider": str(provider_name), "pattern": str(raw), "error": str(exc)})
    return errors


def run_validation() -> dict[str, Any]:
    ecosystem_engine_text = ECOSYSTEM_ENGINE.read_text(encoding="utf-8", errors="replace")
    state_data_engine_text = STATE_DATA_ENGINE.read_text(encoding="utf-8", errors="replace")
    ecosystem_policy_text = ECOSYSTEM_POLICY.read_text(encoding="utf-8", errors="replace") if ECOSYSTEM_POLICY.exists() else ""
    state_data_policy_text = STATE_DATA_POLICY.read_text(encoding="utf-8", errors="replace") if STATE_DATA_POLICY.exists() else ""
    ecosystem_policy = load_json_file(ECOSYSTEM_POLICY, {})
    state_data_policy = load_json_file(STATE_DATA_POLICY, {})
    if not isinstance(ecosystem_policy, dict):
        ecosystem_policy = {}
    if not isinstance(state_data_policy, dict):
        state_data_policy = {}

    ecosystem_contract = ecosystem_policy.get("validation_contract", {})
    state_data_contract = state_data_policy.get("validation_contract", {})
    ecosystem_tokens = _policy_list(ecosystem_contract.get("required_policy_tokens"))
    state_data_tokens = _policy_list(state_data_contract.get("required_policy_tokens"))
    required_data_cache_providers = _policy_list(ecosystem_contract.get("required_data_cache_providers"))
    required_invalidation_tokens = _policy_list(state_data_contract.get("required_client_invalidation_tokens"))
    forbidden_invalidation_tokens = _policy_list(state_data_contract.get("forbidden_client_invalidation_tokens"))
    ecosystem_engine_hits = [token for token in ecosystem_tokens if token in ecosystem_engine_text]
    state_data_engine_hits = [token for token in state_data_tokens if token in state_data_engine_text]
    ecosystem_reference = ecosystem_policy.get("reference_surfaces", {})
    state_reference = state_data_policy.get("reference_surfaces", {})
    data_providers = ecosystem_policy.get("data_cache_providers", {})
    provider_pattern_errors = _provider_pattern_errors(data_providers)

    checks = [
        _check(
            "react_ecosystem_engine_has_no_policy_owned_tokens",
            not ecosystem_engine_hits,
            "Provider/reference-surface tokens should live in config/react_ecosystem_policy.json.",
            ecosystem_engine_hits,
        ),
        _check(
            "react_ecosystem_policy_contains_provider_tokens",
            isinstance(ecosystem_tokens, list) and len(ecosystem_tokens) > 0 and not _missing(ecosystem_tokens, ecosystem_policy_text),
            "Provider/reference-surface tokens should remain explicit and reviewable in policy.",
            _missing(ecosystem_tokens, ecosystem_policy_text),
        ),
        _check(
            "react_ecosystem_reference_surfaces_are_configured",
            isinstance(ecosystem_reference, dict)
            and isinstance(ecosystem_reference.get("docs_demo_path_markers"), list)
            and isinstance(ecosystem_reference.get("test_fixture_path_markers"), list)
            and isinstance(ecosystem_reference.get("test_fixture_file_suffixes"), list)
            and len(ecosystem_reference.get("docs_demo_path_markers") or []) > 0
            and len(ecosystem_reference.get("test_fixture_path_markers") or []) > 0
            and len(ecosystem_reference.get("test_fixture_file_suffixes") or []) > 0,
            "React ecosystem source-context calibration must be policy-backed.",
            ecosystem_reference,
        ),
        _check(
            "react_ecosystem_data_cache_providers_are_configured",
            isinstance(data_providers, dict)
            and bool(required_data_cache_providers)
            and all(key in data_providers for key in required_data_cache_providers),
            "Data-cache provider risk/action selection must be policy-backed.",
            {
                "required": required_data_cache_providers,
                "configured": sorted(data_providers.keys()) if isinstance(data_providers, dict) else [],
            },
        ),
        _check(
            "react_ecosystem_data_cache_provider_patterns_compile",
            not provider_pattern_errors,
            "Data-cache provider regex patterns must not silently disappear from analyzer coverage.",
            provider_pattern_errors,
        ),
        _check(
            "state_data_graph_engine_has_no_policy_owned_tokens",
            not state_data_engine_hits,
            "Reference-surface and invalidation tokens should live in config/state_data_graph_policy.json.",
            state_data_engine_hits,
        ),
        _check(
            "state_data_graph_policy_contains_migrated_tokens",
            isinstance(state_data_tokens, list) and len(state_data_tokens) > 0 and not _missing(state_data_tokens, state_data_policy_text),
            "State/data graph migrated tokens should remain explicit and reviewable in policy.",
            _missing(state_data_tokens, state_data_policy_text),
        ),
        _check(
            "state_data_graph_reference_and_invalidation_policy_configured",
            isinstance(state_reference, dict)
            and isinstance(state_reference.get("path_markers"), list)
            and isinstance(state_reference.get("file_suffixes"), list)
            and isinstance(state_data_policy.get("client_invalidation_tokens"), list)
            and isinstance(state_data_policy.get("key_token_stopwords"), list)
            and len(state_reference.get("path_markers") or []) > 0
            and len(state_reference.get("file_suffixes") or []) > 0
            and len(state_data_policy.get("client_invalidation_tokens") or []) > 0
            and bool(required_invalidation_tokens)
            and all(token in (state_data_policy.get("client_invalidation_tokens") or []) for token in required_invalidation_tokens)
            and not any(token in (state_data_policy.get("client_invalidation_tokens") or []) for token in forbidden_invalidation_tokens),
            "State/data graph source-context and invalidation semantics must be policy-backed.",
            {
                "reference_surfaces": state_reference,
                "client_invalidation_tokens": state_data_policy.get("client_invalidation_tokens"),
                "required_client_invalidation_tokens": required_invalidation_tokens,
                "forbidden_client_invalidation_tokens": forbidden_invalidation_tokens,
                "key_token_stopwords": state_data_policy.get("key_token_stopwords"),
            },
        ),
    ]

    payload = {
        "meta": {"kind": "react_ecosystem_policy_core_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "react_ecosystem_policy_core_validation.json", payload)

    lines = [
        "# React Ecosystem Policy Core Validation",
        "",
        f"- total_checks: `{payload['summary']['total_checks']}`",
        f"- passed_checks: `{payload['summary']['passed_checks']}`",
        f"- failed_checks: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {check['details']} |")
    save_text_atomic(REPORTS_DIR / "react_ecosystem_policy_core_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
