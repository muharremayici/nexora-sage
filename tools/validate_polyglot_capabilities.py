from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

CODE_MAPS_DIR_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR_FOR_IMPORT))

from tools.core.artifact_validator import ensure_against_schema
from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.engines.ast_sequencer_cs import sequence_cs_file_with_evidence
from tools.engines.ast_sequencer_go import sequence_go_file_with_evidence
from tools.engines.ast_sequencer_java import sequence_java_file_with_evidence
from tools.engines.ast_sequencer_python import sequence_python_file_with_evidence


CAPABILITY_PATH = CONFIG_DIR / "polyglot_capabilities.json"
SCHEMA_PATH = CONFIG_DIR / "schemas" / "polyglot_capabilities.schema.json"
LANGUAGE_REGISTRY_PATH = CONFIG_DIR / "language_registry.json"
def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _node_meta(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["node", str(CODE_MAPS_DIR / "tools" / "engines" / "ast_sequencer.cjs"), str(path)],
        cwd=str(CODE_MAPS_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {"parserStatus": "unavailable", "execution_error": result.stderr.strip() or f"rc={result.returncode}"}
    symbols = json.loads(result.stdout)
    return next(
        (item for item in symbols if isinstance(item, dict) and item.get("name") == "__file_meta__"),
        {"parserStatus": "unavailable", "execution_error": "file metadata missing"},
    )


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    capabilities = _read_json(CAPABILITY_PATH)
    registry = _read_json(LANGUAGE_REGISTRY_PATH)

    try:
        ensure_against_schema(SCHEMA_PATH, "polyglot_capabilities", capabilities)
        schema_errors: list[str] = []
    except Exception as exc:
        schema_errors = [str(exc)]
    checks.append(_check("polyglot_capability_schema_valid", not schema_errors, schema_errors or "schema ok"))

    languages = capabilities.get("languages", {})
    registry_languages = registry.get("languages", {})
    validation_contract = capabilities.get("validation_contract", {})
    required_levels = validation_contract.get("required_claim_levels", {})
    compiler_grade_forbidden_claims = validation_contract.get("compiler_grade_forbidden_claims", {})
    required_not_claimed = validation_contract.get("required_not_claimed_by_language", {})
    required_roadmap_candidates = validation_contract.get("required_roadmap_candidates_by_language", {})
    human_surface_required_phrases = validation_contract.get("human_surface_required_phrases", {})
    required_adapters = validation_contract.get("required_roadmap_adapters", [])
    missing_languages = sorted(set(required_levels) - set(languages))
    wrong_levels = {
        name: {"expected": level, "actual": languages.get(name, {}).get("claim_level")}
        for name, level in required_levels.items()
        if languages.get(name, {}).get("claim_level") != level
    }
    checks.append(
        _check(
            "claim_levels_match_positioning",
            not missing_languages and not wrong_levels,
            {"missing_languages": missing_languages, "wrong_levels": wrong_levels},
        )
    )

    extension_mismatches: dict[str, Any] = {}
    for name in required_levels:
        capability_exts = sorted(languages.get(name, {}).get("supported_extensions", []))
        registry_exts = sorted(registry_languages.get(name, {}).get("extensions", []))
        if capability_exts != registry_exts:
            extension_mismatches[name] = {"capability": capability_exts, "registry": registry_exts}
    checks.append(
        _check(
            "capability_extensions_follow_language_registry",
            not extension_mismatches,
            extension_mismatches or "all capability extensions match language_registry.json",
        )
    )

    missing_forbidden_tokens: dict[str, list[str]] = {}
    accidental_compiler_claims: dict[str, list[str]] = {}
    for language, forbidden_claims in compiler_grade_forbidden_claims.items():
        not_claimed = set(languages.get(language, {}).get("not_claimed", []))
        evidence = set(languages.get(language, {}).get("evidence", []))
        missing = sorted(set(forbidden_claims) - not_claimed)
        if missing:
            missing_forbidden_tokens[language] = missing
        claimed = sorted(set(forbidden_claims) & evidence)
        if claimed:
            accidental_compiler_claims[language] = claimed
    checks.append(
        _check(
            "compiler_grade_claims_are_explicitly_not_claimed",
            not missing_forbidden_tokens and not accidental_compiler_claims,
            {
                "missing_not_claimed": missing_forbidden_tokens,
                "accidental_evidence_claims": accidental_compiler_claims,
            },
        )
    )

    missing_language_boundaries: dict[str, list[str]] = {}
    for language, required in required_not_claimed.items():
        declared = set(languages.get(language, {}).get("not_claimed", []))
        missing = sorted(set(required) - declared)
        if missing:
            missing_language_boundaries[language] = missing
    checks.append(
        _check(
            "language_specific_non_claims_are_complete",
            not missing_language_boundaries,
            missing_language_boundaries or "all required language-specific non-claims are explicit",
        )
    )

    missing_roadmap_candidates: dict[str, list[str]] = {}
    for language, required in required_roadmap_candidates.items():
        declared = set(languages.get(language, {}).get("roadmap_candidates", []))
        missing = sorted(set(required) - declared)
        if missing:
            missing_roadmap_candidates[language] = missing
    checks.append(
        _check(
            "language_roadmap_candidates_are_explicit",
            not missing_roadmap_candidates,
            missing_roadmap_candidates or "all required language roadmap candidates are explicit",
        )
    )

    missing_human_surface_phrases: dict[str, list[str]] = {}
    for relative_path, required_phrases in human_surface_required_phrases.items():
        surface_text = _read(CODE_MAPS_DIR / relative_path)
        missing = [phrase for phrase in required_phrases if phrase not in surface_text]
        if missing:
            missing_human_surface_phrases[relative_path] = missing
    checks.append(
        _check(
            "human_surfaces_state_honest_positioning",
            not missing_human_surface_phrases,
            missing_human_surface_phrases or "all governed human surfaces contain conservative positioning",
        )
    )

    required_roadmap_adapters = set(capabilities.get("release_guardrails", {}).get("required_roadmap_adapters", []))
    checks.append(
        _check(
            "semantic_adapter_roadmap_is_explicit",
            set(required_adapters).issubset(required_roadmap_adapters),
            sorted(required_roadmap_adapters),
        )
    )

    with tempfile.TemporaryDirectory(prefix="polyglot_python_claim_") as tmp:
        root = Path(tmp)
        valid_path = root / "valid.py"
        invalid_path = root / "invalid.py"
        missing_path = root / "missing.py"
        valid_path.write_text("def compute(value):\n    return value + 1\n", encoding="utf-8")
        invalid_path.write_text("def recoverable(:\n    return 1\n", encoding="utf-8")
        observed = sequence_python_file_with_evidence(str(valid_path))
        degraded = sequence_python_file_with_evidence(str(invalid_path))
        unavailable = sequence_python_file_with_evidence(str(missing_path))
    checks.append(
        _check(
            "python_ast_claim_requires_observed_run_evidence",
            observed.get("status") == "observed"
            and observed.get("parser_kind") == "python_ast"
            and observed.get("semantic_depth") == "ast_normalized"
            and bool(observed.get("symbols")),
            {key: observed.get(key) for key in ("status", "parser_kind", "semantic_depth", "error_family")},
        )
    )
    checks.append(
        _check(
            "python_ast_fallback_is_explicit_degradation",
            degraded.get("status") == "degraded"
            and degraded.get("parser_kind") == "regex_fallback"
            and degraded.get("semantic_depth") == "unavailable"
            and degraded.get("error_family") == "python_ast_processing_error",
            {key: degraded.get(key) for key in ("status", "parser_kind", "semantic_depth", "error_family")},
        )
    )
    checks.append(
        _check(
            "python_source_read_failure_is_unavailable_not_empty_fact",
            unavailable.get("status") == "unavailable"
            and unavailable.get("parser_kind") == "unavailable"
            and unavailable.get("error_family") == "source_read_error"
            and unavailable.get("symbols") == [],
            {key: unavailable.get(key) for key in ("status", "parser_kind", "semantic_depth", "error_family")},
        )
    )
    structural_unavailable = {
        "java": sequence_java_file_with_evidence(str(root / "missing.java")),
        "go": sequence_go_file_with_evidence(str(root / "missing.go")),
        "csharp": sequence_cs_file_with_evidence(str(root / "missing.cs")),
    }
    invalid_structural_fallbacks = {
        language: {
            key: result.get(key)
            for key in ("status", "parser_kind", "semantic_depth", "error_family")
        }
        for language, result in structural_unavailable.items()
        if result.get("status") != "unavailable"
        or result.get("parser_kind") != "unavailable"
        or result.get("error_family") != "source_read_error"
        or result.get("symbols") != []
    }
    checks.append(
        _check(
            "structural_source_read_failures_are_unavailable_not_empty_facts",
            not invalid_structural_fallbacks,
            invalid_structural_fallbacks or "Java, Go and C# report unavailable source evidence explicitly",
        )
    )

    with tempfile.TemporaryDirectory(prefix="polyglot_node_claim_") as tmp:
        node_root = Path(tmp)
        valid_ts = node_root / "valid.ts"
        invalid_ts = node_root / "invalid.ts"
        missing_ts = node_root / "missing.ts"
        valid_ts.write_text("export const compute = (value: number) => value + 1;\n", encoding="utf-8")
        invalid_ts.write_text("export const broken = (: number) => 1;\n", encoding="utf-8")
        node_observed = _node_meta(valid_ts)
        node_degraded = _node_meta(invalid_ts)
        node_unavailable = _node_meta(missing_ts)
    checks.append(
        _check(
            "node_ast_claim_requires_observed_run_evidence",
            node_observed.get("parserStatus") == "observed"
            and node_observed.get("parserKind") == "typescript_compiler_api"
            and node_observed.get("parserDiagnosticCount") == 0,
            {key: node_observed.get(key) for key in ("parserStatus", "parserKind", "semanticDepth", "parserDiagnosticCount")},
        )
    )
    checks.append(
        _check(
            "node_parse_diagnostics_are_explicit_degradation",
            node_degraded.get("parserStatus") == "degraded"
            and node_degraded.get("parserDiagnosticCount", 0) > 0
            and node_degraded.get("semanticDepth") == "partial_ast",
            {key: node_degraded.get(key) for key in ("parserStatus", "parserKind", "semanticDepth", "parserDiagnosticCount")},
        )
    )
    checks.append(
        _check(
            "node_source_read_failure_is_explicitly_unavailable",
            node_unavailable.get("parserStatus") == "unavailable"
            and node_unavailable.get("semanticDepth") == "unavailable"
            and "Error:FileReadFailed" in node_unavailable.get("features", []),
            {key: node_unavailable.get(key) for key in ("parserStatus", "parserKind", "semanticDepth", "features")},
        )
    )

    summary = {
        "total_checks": len(checks),
        "passed_checks": sum(1 for check in checks if check["passed"]),
        "failed_checks": sum(1 for check in checks if not check["passed"]),
    }
    payload = {
        "meta": {"kind": "polyglot_capability_validation", "version": "v1"},
        "summary": summary,
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "polyglot_capability_validation.json", payload)

    lines = [
        "# Polyglot Capability Validation",
        "",
        f"- Total checks: `{summary['total_checks']}`",
        f"- Passed: `{summary['passed_checks']}`",
        f"- Failed: `{summary['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {escaped_details} |")
    save_text_atomic(REPORTS_DIR / "polyglot_capability_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
