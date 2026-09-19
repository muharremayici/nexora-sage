from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.target_repository_trust import (
    classify_target_path,
    load_target_repository_threat_boundary_contract,
)


RAW_OUTPUT_PATH = RAW_DIR / "target_repository_threat_boundary_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "target_repository_threat_boundary_validation.md"


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _read(relative: str) -> str:
    path = CODE_MAPS_DIR / relative
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def build_validation() -> dict[str, Any]:
    contract = load_target_repository_threat_boundary_contract()
    trust_classes = contract.get("trust_classes") if isinstance(contract.get("trust_classes"), dict) else {}
    action_matrix = contract.get("action_matrix") if isinstance(contract.get("action_matrix"), dict) else {}
    compiler = contract.get("typescript_static_compiler_projection") if isinstance(contract.get("typescript_static_compiler_projection"), dict) else {}
    contained = classify_target_path(CODE_MAPS_DIR, CODE_MAPS_DIR / "config")
    escaping = classify_target_path(CODE_MAPS_DIR, CODE_MAPS_DIR / "..")
    oracle_text = _read("tools/engines/validation_oracle.py")
    collector_text = _read("tools/engines/ts_diagnostics_collector.cjs")
    preflight_text = _read("tools/external_target_preflight.py")
    checks = [
        _check("trust_classes_are_complete", set(trust_classes) == {"operator_trusted", "ordinary_unverified", "adversarial_or_hostile"}, sorted(trust_classes)),
        _check("default_is_ordinary_unverified", contract.get("default_trust_class") == "ordinary_unverified", contract.get("default_trust_class")),
        _check("threat_categories_are_complete", {"repository_bytes", "repository_config", "dependency_metadata", "subprocesses", "prompt_like_text", "secret_bearing_content", "resource_exhaustion"}.issubset(action_matrix), sorted(action_matrix)),
        _check("path_classifier_accepts_contained", contained.get("contained") is True, contained),
        _check("path_classifier_rejects_escape", escaping.get("contained") is False, escaping),
        _check("preflight_projects_trust_and_path_attention", "target_trust_projection(" in preflight_text and "escaping_target_paths_excluded" in preflight_text and "target_trust_class_not_supported" in preflight_text, "preflight trust/path markers"),
        _check("inventory_and_topology_share_path_boundary", "path_boundary_state" in _read("tools/core/target_inventory.py") and "path_boundary_state" in _read("tools/core/repository_topology.py"), "shared target path boundary state"),
        _check(
            "atlas_filters_full_walk_paths",
            "excluded_roots=excluded_project_roots" in _read("tools/engines/generate_atlas.py"),
            "Atlas full-walk containment",
        ),
        _check(
            "oracle_uses_sage_owned_compiler_only",
            "CODE_MAPS_DIR / \"tools\" / \"engines\" / \"node_modules\"" in oracle_text
            and "command is not None and list(command) != cmd" in oracle_text
            and "allow_oracle_npx_fallback" not in oracle_text
            and "node_modules_bin" not in oracle_text,
            "direct SAGE TypeScript command with caller-command refusal",
        ),
        _check(
            "oracle_filters_target_compiler_options",
            "allowed_literal_compiler_options" in oracle_text
            and "sanctuary_aliases" in oracle_text
            and "is_target_path_contained(source_root, source_tsconfig)" in oracle_text
            and "not is_target_path_contained(source_root, source_path)" in oracle_text,
            sorted(compiler.get("allowed_literal_compiler_options", [])),
        ),
        _check(
            "ts_diagnostics_has_bounded_parse_and_semantic_hosts",
            "boundedParseHost(workspaceRoot)" in collector_text
            and "boundedCompilerHost(parsed.options, workspaceRoot)" in collector_text
            and "isResolvedPathWithin(workspaceRoot, fileName)" in collector_text
            and "BOUNDARY_REJECTED" in collector_text,
            "bounded TypeScript config, root-file and semantic-program hosts",
        ),
        _check("agent_surfaces_label_repository_text_untrusted", "repository_content_trust" in _read("tools/core/contextos_mcp.py") and "repository_content_trust" in _read("tools/mcp/server.py"), "ContextOS and MCP"),
        _check("proof_surfaces_threat_boundary", "target_repository_threat_boundary" in _read("tools/core/target_repository_proof.py"), "target proof subject projection"),
        _check("hostile_safety_is_not_claimed", "hostile-repository safety" in str(contract.get("claim_boundary") or "") and action_matrix.get("resource_exhaustion", {}).get("hostile_input_resistance") == "not_available", contract.get("claim_boundary")),
    ]
    failures = [row for row in checks if not row["passed"]]
    return {
        "meta": {
            "kind": "target_repository_threat_boundary_validation",
            "version": "v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "tools.validate_target_repository_threat_boundary",
        },
        "summary": {"status": "FAIL" if failures else "PASS", "checks": len(checks), "passed": len(checks) - len(failures), "failed": len(failures)},
        "checks": checks,
    }


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Target Repository Threat Boundary Validation",
        "",
        f"- status: `{payload['summary']['status']}`",
        f"- checks: `{payload['summary']['checks']}`",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    lines.extend(f"| `{row['name']}` | {'PASS' if row['passed'] else 'FAIL'} |" for row in payload["checks"])
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
