from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import CONFIG_DIR, DOCTRINE, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


DEAD_CODE_ENGINE = CODE_MAPS_DIR / "tools" / "engines" / "dead_code_detector.py"
CONTRACT_PATH = CONFIG_DIR / "dead_code_language_agnostic_core_contract.json"


def _check(name: str, passed: bool, details: str, evidence: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "details": details,
        "evidence": evidence,
    }


def _contract_list(contract: dict[str, Any], key: str) -> list[str]:
    values = contract.get(key, []) if isinstance(contract, dict) else []
    return [str(item) for item in values if str(item or "").strip()] if isinstance(values, list) else []


def _doctrine_owned_surface_tokens(heuristics: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    fields_by_section = contract.get("doctrine_owned_surface_token_fields", {}) if isinstance(contract, dict) else {}
    if not isinstance(fields_by_section, dict):
        fields_by_section = {}
    for section_name, field_names in fields_by_section.items():
        section = heuristics.get(section_name, {})
        if not isinstance(section, dict):
            continue
        for field_name in field_names:
            values = section.get(field_name, [])
            if isinstance(values, list):
                tokens.extend(str(item) for item in values if str(item or "").strip())
    return sorted(
        {
            token
            for token in tokens
            if "/" in token or "." in token or token.startswith("import.")
        }
    )


def run_validation() -> dict[str, Any]:
    source = DEAD_CODE_ENGINE.read_text(encoding="utf-8", errors="replace")
    lowered = source.lower()
    contract = load_json_file(CONTRACT_PATH, {})
    heuristics = DOCTRINE.get("dead_code_heuristics", {})
    if not isinstance(heuristics, dict):
        heuristics = {}

    forbidden_tokens = _contract_list(contract, "forbidden_engine_tokens")
    required_sections = _contract_list(contract, "required_heuristic_sections")
    forbidden_hits = [token for token in forbidden_tokens if token in lowered]
    policy_owned_tokens = _doctrine_owned_surface_tokens(heuristics, contract)
    policy_token_hits = [token for token in policy_owned_tokens if token.lower() in lowered]
    missing_sections = [section for section in required_sections if section not in heuristics]

    checks = [
        _check(
            "dead_code_language_core_contract_loaded",
            bool(forbidden_tokens) and bool(required_sections),
            "Validator scope must be machine-readable and fail closed when the contract is missing or empty.",
            {
                "contract": "config/dead_code_language_agnostic_core_contract.json",
                "forbidden_tokens": len(forbidden_tokens),
                "required_sections": len(required_sections),
            },
        ),
        _check(
            "dead_code_engine_has_no_repo_specific_legacy_tokens",
            not forbidden_hits,
            "Dead-code core must not carry private-host or old project-specific contract exceptions.",
            forbidden_hits,
        ),
        _check(
            "dead_code_contract_policy_is_doctrine_backed",
            not missing_sections,
            "Dead-code policy sections must live in architecture_doctrine.json rather than in repo-specific engine branches.",
            missing_sections,
        ),
        _check(
            "dead_code_engine_has_no_policy_owned_surface_tokens",
            not policy_token_hits,
            "Dead-code path/example/generated surface tokens should live in architecture_doctrine.json.",
            policy_token_hits,
        ),
        _check(
            "contract_registry_is_available",
            isinstance(heuristics.get("contract_surface_registry"), list)
            and len(heuristics.get("contract_surface_registry") or []) >= 1,
            "Contract-only surfaces should be expressed through a registry.",
            {"rules": len(heuristics.get("contract_surface_registry") or [])},
        ),
        _check(
            "runtime_consumed_contracts_are_configured",
            isinstance(heuristics.get("runtime_consumed_contracts"), list),
            "Runtime-discovered contracts should be doctrine-configured.",
            {"rules": len(heuristics.get("runtime_consumed_contracts") or [])},
        ),
    ]

    payload = {
        "meta": {"kind": "dead_code_language_agnostic_core_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "dead_code_language_agnostic_core_validation.json", payload)

    lines = [
        "# Dead Code Language-Agnostic Core Validation",
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
    save_text_atomic(REPORTS_DIR / "dead_code_language_agnostic_core_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
