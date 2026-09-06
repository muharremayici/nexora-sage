from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.config import ROOT as TARGET_ROOT
from tools.core.genome_io import load_genome_data


CODE_MAPS_DIR = ROOT


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _iter_genome_entries(genome: Any):
    if not isinstance(genome, dict):
        return
    for values in genome.values():
        if not isinstance(values, list):
            continue
        for entry in values:
            if isinstance(entry, dict):
                yield entry


def _sample_genome_byte_span() -> dict[str, Any]:
    genome = load_genome_data(default={})
    candidates = [
        entry
        for entry in _iter_genome_entries(genome)
        if entry.get("project") == "MAIN"
        and entry.get("workspace_rel")
        and isinstance(entry.get("start"), int)
        and isinstance(entry.get("end"), int)
    ]
    candidates.sort(
        key=lambda entry: (
            "..." in str(entry.get("signature") or ""),
            str(entry.get("workspace_rel") or ""),
            int(entry.get("start") or 0),
        )
    )
    for entry in candidates:
        workspace_rel = str(entry.get("workspace_rel") or "").replace("\\", "/").lstrip("/")
        source_path = (TARGET_ROOT / workspace_rel).resolve()
        try:
            source_path.relative_to(TARGET_ROOT.resolve())
        except ValueError:
            continue
        if not source_path.exists():
            continue
        source_bytes = source_path.read_bytes()
        start = int(entry.get("start") or 0)
        end = int(entry.get("end") or 0)
        if start < 0 or end <= start or end > len(source_bytes):
            return {
                "status": "fail",
                "reason": "byte span is outside current source file bounds",
                "symbol": entry.get("name"),
                "workspace_rel": workspace_rel,
                "start": start,
                "end": end,
                "source_bytes": len(source_bytes),
            }
        snippet = source_bytes[start:end].decode("utf-8", errors="replace")
        signature = str(entry.get("signature") or "").strip()
        exact_signature_expected = bool(signature and "..." not in signature)
        signature_matches = (not exact_signature_expected) or signature in snippet
        return {
            "status": "pass" if snippet.strip() and signature_matches else "fail",
            "symbol": entry.get("name"),
            "type": entry.get("type"),
            "workspace_rel": workspace_rel,
            "scoped_workspace_rel": entry.get("scoped_workspace_rel"),
            "start": start,
            "end": end,
            "source_bytes": len(source_bytes),
            "decoded_snippet": snippet[:160],
            "signature": signature,
            "exact_signature_expected": exact_signature_expected,
            "signature_matches": signature_matches,
            "offset_contract": "Genome start/end are UTF-8 byte offsets, not line numbers or Python character offsets.",
        }
    return {"status": "skipped", "reason": "no MAIN genome entry with source byte offsets found"}


def run_validation() -> dict[str, Any]:
    atlas_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "generate_atlas.py")
    nuclear_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "nuclear_processor.py")
    audit_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "audit.py")
    audit_report_text = _read(CODE_MAPS_DIR / "tools" / "core" / "audit_report.py")
    artifact_store_text = _read(CODE_MAPS_DIR / "tools" / "core" / "artifact_store.py")
    source_files_text = _read(CODE_MAPS_DIR / "tools" / "core" / "source_files.py")

    required_atlas_symbol_fields = [
        "logic_dna",
        "semantic_signature",
        "canonical_symbol_type",
        "framework_tags",
        "normalization_profile",
        "semantic_depth",
        "logic_dna_kind",
        "normalization_confidence",
        "parser_kind",
        "parser_version",
        "member_details",
        "dependency_imports",
        "ui_dependencies",
        "architectural_markers",
        "runtime_contract",
        "runtime_contract_kind",
    ]
    nuclear_consumed_fields = [
        "logic_dna",
        "semantic_signature",
        "canonical_symbol_type",
        "framework_tags",
        "normalization_profile",
        "semantic_depth",
        "logic_dna_kind",
        "normalization_confidence",
        "parser_kind",
        "parser_version",
        "member_details",
        "dependency_imports",
        "ui_dependencies",
        "architectural_markers",
        "runtime_contract",
        "runtime_contract_kind",
    ]

    checks = [
        _check(
            "atlas_declares_current_symbol_contract_fields",
            all(f'"{field}"' in atlas_text or f"'{field}'" in atlas_text for field in required_atlas_symbol_fields)
            and "file_contract_is_current" in atlas_text,
            {"required_fields": required_atlas_symbol_fields},
        ),
        _check(
            "atlas_file_cache_contract_requires_agent_path_fields",
            all(
                f'"{field}"' in atlas_text or f"'{field}'" in atlas_text
                for field in ("project_key", "atlas_rel_path", "workspace_rel", "repo_relative_path", "target_ref")
            )
            and "required_file_keys" in atlas_text
            and "required_file_keys.issubset(file_data.keys())" in atlas_text,
            "Cached Atlas file entries must carry graph, editor and MCP path fields before they can be lifted.",
        ),
        _check(
            "nuclear_prefers_atlas_symbols_before_regex_fallback",
            "atlas_file_data and atlas_file_data.get('symbols')" in nuclear_text
            and "if blocks: return blocks" in nuclear_text
            and "regex_fallback_v1" in nuclear_text,
            "Genome should enrich Atlas AST symbols first and use regex only as fallback.",
        ),
        _check(
            "nuclear_preserves_atlas_contract_fields",
            all(field in nuclear_text for field in nuclear_consumed_fields)
            and "_cached_blocks_are_current" in nuclear_text,
            {"consumed_fields": nuclear_consumed_fields},
        ),
        _check(
            "atlas_and_nuclear_share_source_classifier",
            "is_analysis_source_file" in atlas_text
            and "is_analysis_source_file" in nuclear_text
            and "non_source_template_extensions()" in source_files_text,
            "Atlas and Nuclear should share the registry-governed source universe.",
        ),
        _check(
            "audit_is_atlas_only_for_rule_evidence",
            "from tools.core.atlas_io import load_atlas_data, resolve_atlas_data" in audit_text
            and "def analyze_project(changed_files=None, atlas=None):" in audit_text
            and "atlas, atlas_input_source = resolve_atlas_data(atlas)" in audit_text
            and "a_data.get(\"features\"" in audit_text
            and "a_data.get(\"symbols\"" in audit_text
            and "import_records" in audit_text
            and "open(" not in audit_text,
            "Audit should consume same-execution Atlas when provided and otherwise fall back to SQLite-first evidence without reparsing source files.",
        ),
        _check(
            "audit_outputs_structured_report_and_invalidates_cache",
            "AUDIT_REPORT_JSON_PATH" in audit_text
            and "invalidate_audit_report_cache()" in audit_text
            and "'violations': structured_violations" in audit_text,
            "Audit report should be structured and cache invalidation should happen after writes.",
        ),
        _check(
            "downstream_audit_reads_use_cached_helper",
            "path = RAW_DIR / \"audit_report.json\"" in audit_report_text
            and "load_json_file(path" in audit_report_text
            and "invalidate_audit_report_cache" in audit_report_text,
            "Downstream engines should use the audit_report helper for cached structured access.",
        ),
        _check(
            "sqlite_store_indexes_atlas_relations_and_symbols",
            "INSERT OR IGNORE INTO dependencies" in artifact_store_text
            and "INSERT INTO symbols" in artifact_store_text
            and "proj_data.get(\"dependencies\"" in artifact_store_text
            and "proj_data.get(\"symbols\"" in artifact_store_text,
            "SQLite shadow/SSoT should retain Atlas dependency and symbol query surfaces.",
        ),
    ]
    byte_span_sample = _sample_genome_byte_span()
    checks.append(
        _check(
            "genome_byte_offsets_resolve_to_current_source_slice",
            byte_span_sample.get("status") == "pass",
            byte_span_sample,
        )
    )
    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {"kind": "atlas_genome_audit_integrity_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "atlas_genome_audit_integrity_validation.json", payload)

    lines = [
        "# Atlas -> Genome/Audit Integrity Validation",
        "",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        details = str(check.get("details", "")).replace("|", "\\|")
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {details} |")
    save_text_atomic(REPORTS_DIR / "atlas_genome_audit_integrity_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
