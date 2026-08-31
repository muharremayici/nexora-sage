from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.artifact_validator import validate_against_schema
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, SCHEMAS_DIR, save_json_atomic, save_text_atomic
from tools.core.language_agnostic_symbols import canonical_symbol_type
from tools.core.subprocess_telemetry import run_observed_subprocess
from tools.engines.ast_sequencer_python import sequence_python_file
from tools.engines.generate_atlas import _normalize_polyglot_symbol


CONFIG_PATH = CONFIG_DIR / "language_agnostic_symbols.json"
SCHEMA_PATH = SCHEMAS_DIR / "language_agnostic_symbols.schema.json"
SEQUENCER = CODE_MAPS_DIR / "tools" / "engines" / "ast_sequencer.cjs"


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details if details is not None else {}}


def _validation_requirements(config: dict[str, Any]) -> dict[str, Any]:
    requirements = config.get("validation_requirements", {}) if isinstance(config, dict) else {}
    return requirements if isinstance(requirements, dict) else {}


def _required_languages(config: dict[str, Any]) -> set[str]:
    values = _validation_requirements(config).get("required_languages", [])
    return {str(value) for value in values if str(value).strip()} if isinstance(values, list) else set()


def _core_type_expectations(config: dict[str, Any]) -> list[dict[str, str]]:
    values = _validation_requirements(config).get("core_type_expectations", [])
    if not isinstance(values, list):
        return []
    return [
        {
            "raw_type": str(item.get("raw_type") or ""),
            "language": str(item.get("language") or ""),
            "expected": str(item.get("expected") or ""),
        }
        for item in values
        if isinstance(item, dict)
    ]


def _run_sequencer(path: Path) -> list[dict[str, Any]]:
    from tools.core.artifact_store import get_adaptive_timeout

    result, _duration = run_observed_subprocess(
        ["node", str(SEQUENCER), str(path)],
        cwd=CODE_MAPS_DIR,
        label="language_agnostic_symbol_fixture_ast",
        timeout=get_adaptive_timeout(180),
        log=print,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"sequencer exited {result.returncode}")
    payload = json.loads(result.stdout)
    return payload if isinstance(payload, list) else []


def _symbol_named(symbols: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for symbol in symbols:
        if isinstance(symbol, dict) and symbol.get("name") == name:
            return symbol
    return {}


def _logic_dna_fixture_check() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nexora_logic_dna_") as tmp:
        root = Path(tmp)
        plain = root / "plain.ts"
        next_action = root / "next_action.ts"
        callback = root / "callback.tsx"
        plain.write_text("export function compute(a: number, b: number) {\n  return a + b;\n}\n", encoding="utf-8")
        next_action.write_text('"use server";\nexport async function compute(a: number, b: number) { return a+b; }\n', encoding="utf-8")
        callback.write_text(
            "import { useCallback } from 'react';\n"
            "export const compute = useCallback((a: number, b: number) => { return a + b; }, []);\n",
            encoding="utf-8",
        )
        symbols = {
            "plain": _symbol_named(_run_sequencer(plain), "compute"),
            "next_action": _symbol_named(_run_sequencer(next_action), "compute"),
            "callback": _symbol_named(_run_sequencer(callback), "compute"),
        }
        logic_values = {key: value.get("logicDna") for key, value in symbols.items()}
        raw_values = {key: value.get("dna") for key, value in symbols.items()}
        framework_tags = {
            key: value.get("frameworkTags", [])
            for key, value in symbols.items()
        }
        canonical_types = {
            key: value.get("canonicalSymbolType")
            for key, value in symbols.items()
        }
        return {
            "symbols": symbols,
            "logic_values": logic_values,
            "raw_values": raw_values,
            "framework_tags": framework_tags,
            "canonical_types": canonical_types,
            "logic_same": len(set(logic_values.values())) == 1 and all(logic_values.values()),
            "raw_not_all_same": len(set(raw_values.values())) > 1,
        }


def _python_logic_dna_fixture_check() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nexora_python_logic_dna_") as tmp:
        root = Path(tmp)
        same_a = root / "same_a.py"
        same_b = root / "same_b.py"
        changed = root / "changed.py"
        same_a.write_text("def compute(value: int):\n    return value + 1\n", encoding="utf-8")
        same_b.write_text("# comment\ndef compute(value: int):\n\n    return value + 1  # same\n", encoding="utf-8")
        changed.write_text("def compute(value: int):\n    return value + 2\n", encoding="utf-8")
        values = []
        for path in (same_a, same_b, changed):
            symbol = _symbol_named(sequence_python_file(str(path)), "compute")
            values.append(symbol)
        return {
            "symbols": values,
            "formatting_stable": values[0].get("logicDna") == values[1].get("logicDna"),
            "logic_sensitive": values[0].get("logicDna") != values[2].get("logicDna"),
        }


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    schema_errors = validate_against_schema(SCHEMA_PATH, "language_agnostic_symbols", config)
    checks.append(_check("language_agnostic_symbol_schema_valid", not schema_errors, schema_errors or "schema ok"))

    required_languages = _required_languages(config)
    type_map = config.get("raw_type_map", {}) if isinstance(config.get("raw_type_map"), dict) else {}
    missing_languages = sorted(required_languages - set(type_map.keys()))
    checks.append(
        _check(
            "language_agnostic_symbol_requirements_are_config_owned",
            bool(required_languages) and bool(_core_type_expectations(config)),
            {
                "required_languages": sorted(required_languages),
                "core_type_expectations": len(_core_type_expectations(config)),
            },
        )
    )
    checks.append(_check("language_agnostic_symbol_map_covers_required_languages", not missing_languages, missing_languages))

    default_profiles = config.get("default_profiles", {}) if isinstance(config.get("default_profiles"), dict) else {}
    profiles = config.get("normalization_profiles", {}) if isinstance(config.get("normalization_profiles"), dict) else {}
    missing_profiles = {
        language: default_profiles.get(language)
        for language in sorted(required_languages)
        if not default_profiles.get(language) or default_profiles.get(language) not in profiles
    }
    checks.append(_check("normalization_profiles_cover_required_languages", not missing_profiles, missing_profiles))

    mismatches = []
    for expectation in _core_type_expectations(config):
        raw_type = expectation.get("raw_type", "")
        language = expectation.get("language", "")
        expected_type = expectation.get("expected", "")
        actual = canonical_symbol_type(raw_type, language)
        if actual != expected_type:
            mismatches.append({"raw_type": raw_type, "language": language, "expected": expected_type, "actual": actual})
    checks.append(_check("language_agnostic_symbol_resolver_maps_core_types", not mismatches, mismatches))

    atlas_path_mismatches = []
    for expectation in _core_type_expectations(config):
        raw_type = expectation.get("raw_type", "")
        language = expectation.get("language", "")
        expected_type = expectation.get("expected", "")
        normalized = _normalize_polyglot_symbol(
            {"name": "Example", "type": raw_type, "signature": f"{raw_type}:Example"},
            "",
            language,
        )
        if normalized.get("canonical_symbol_type") != expected_type:
            atlas_path_mismatches.append({
                "raw_type": raw_type,
                "language": language,
                "expected": expected_type,
                "actual": normalized.get("canonical_symbol_type"),
            })
    checks.append(_check("atlas_normalization_preserves_canonical_types", not atlas_path_mismatches, atlas_path_mismatches))

    fixture = _logic_dna_fixture_check()
    checks.append(
        _check(
            "logic_dna_ignores_framework_noise_for_equivalent_logic",
            fixture["logic_same"] and fixture["raw_not_all_same"],
            {
                "logic_values": fixture["logic_values"],
                "raw_values": fixture["raw_values"],
                "framework_tags": fixture["framework_tags"],
                "canonical_types": fixture["canonical_types"],
            },
        )
    )

    python_fixture = _python_logic_dna_fixture_check()
    checks.append(
        _check(
            "python_ast_logic_dna_is_formatting_stable_and_logic_sensitive",
            python_fixture["formatting_stable"] and python_fixture["logic_sensitive"],
            {
                "logic_values": [item.get("logicDna") for item in python_fixture["symbols"]],
                "profiles": [item.get("normalizationProfile") for item in python_fixture["symbols"]],
            },
        )
    )

    structural_profiles = {
        language: profiles.get(default_profiles.get(language, ""), {})
        for language in ("java", "go", "csharp")
    }
    structural_profiles_honest = all(
        profile.get("semantic_depth") == "signature_only"
        and profile.get("logic_dna_kind") == "structural_signature"
        and profile.get("normalization_confidence") == "low"
        for profile in structural_profiles.values()
    )
    checks.append(_check("structural_language_claims_are_explicitly_bounded", structural_profiles_honest, structural_profiles))

    checks.append(
        _check(
            "logic_dna_symbols_carry_identity_fields",
            all(
                fixture["symbols"][key].get("semanticSignature")
                and fixture["symbols"][key].get("canonicalSymbolType")
                and fixture["symbols"][key].get("normalizationProfile") == "typescript_react_v1"
                for key in fixture["symbols"]
            ),
            {
                key: {
                    "semanticSignature": value.get("semanticSignature"),
                    "canonicalSymbolType": value.get("canonicalSymbolType"),
                    "normalizationProfile": value.get("normalizationProfile"),
                }
                for key, value in fixture["symbols"].items()
            },
        )
    )

    with tempfile.TemporaryDirectory(prefix="nexora_default_export_identity_") as tmp:
        root = Path(tmp)
        module = root / "component.tsx"
        module.write_text("function App() { return null; }\nexport default App;\n", encoding="utf-8")
        default_export = _symbol_named(_run_sequencer(module), "default_export")
    checks.append(
        _check(
            "default_export_preserves_local_export_identity",
            default_export.get("exportKind") == "default"
            and default_export.get("exportedNames") == ["App"]
            and "App" in (default_export.get("dependencies") or []),
            {
                "exportKind": default_export.get("exportKind"),
                "exportedNames": default_export.get("exportedNames"),
                "dependencies": default_export.get("dependencies"),
                "signature": default_export.get("signature"),
            },
        )
    )

    summary = {
        "total_checks": len(checks),
        "passed_checks": sum(1 for check in checks if check["passed"]),
        "failed_checks": sum(1 for check in checks if not check["passed"]),
    }
    payload = {
        "meta": {"kind": "language_agnostic_symbol_validation", "version": "v2"},
        "summary": summary,
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "language_agnostic_symbol_validation.json", payload)
    lines = [
        "# Language-Agnostic Symbol Validation",
        "",
        f"- Total checks: `{summary['total_checks']}`",
        f"- Passed: `{summary['passed_checks']}`",
        f"- Failed: `{summary['failed_checks']}`",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for check in checks:
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} |")
    save_text_atomic(REPORTS_DIR / "language_agnostic_symbol_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
