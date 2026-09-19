from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import OUTPUT_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic, _target_output_slug
from tools.core.agent_packet_budget import BOUNDED_AGENT_PACKET_TOKENS, estimate_tokens, token_budget_class
from tools.core.agent_surface_target_visibility import is_structured_precondition_block
from tools.core.atlas_io import load_atlas_data
from tools.core.external_target_generation import resolve_external_target_artifact_dir
from tools.core.json_io import load_json_file
from tools.core.text_normalizer import SUSPICIOUS_MARKERS, has_suspicious_text
from tools.mcp import server as mcp_server


REPLACEMENT_CHARACTER = "\ufffd"
SEAL_CONTRACT_PATH = ROOT / "config" / "agent_surface_seal_contract.json"


def _quality_review_contract() -> dict[str, Any]:
    contract = load_json_file(SEAL_CONTRACT_PATH, {})
    policy = contract.get("quality_review_contract", {}) if isinstance(contract, dict) else {}
    return policy if isinstance(policy, dict) else {}


def _negative_probe_samples() -> dict[str, str]:
    values = _quality_review_contract().get("negative_probe_purposes", {})
    return {str(name): str(purpose) for name, purpose in values.items()} if isinstance(values, dict) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _raw_dir_for_review(target_root: str = "") -> Path:
    if not target_root:
        return RAW_DIR
    target_dir = OUTPUT_DIR / "external_targets" / _target_output_slug(str(Path(target_root).resolve()))
    artifact_dir, _reason = resolve_external_target_artifact_dir(target_dir)
    return artifact_dir / ".raw"


def _reports_dir_for_review(target_root: str = "") -> Path:
    if not target_root:
        return REPORTS_DIR
    target_dir = OUTPUT_DIR / "external_targets" / _target_output_slug(str(Path(target_root).resolve()))
    artifact_dir, _reason = resolve_external_target_artifact_dir(target_dir)
    return artifact_dir / "reports"


def _analysis_root_for_review(target_root: str = "") -> Path:
    if target_root:
        return Path(target_root).resolve()
    return ROOT.parent.resolve()


def _forbidden_default_markers() -> list[str]:
    contract = load_json_file(SEAL_CONTRACT_PATH, {})
    values = contract.get("default_forbidden_markers", []) if isinstance(contract, dict) else []
    return [str(item) for item in values if str(item or "").strip()]


def _manual_pack_selection_policy() -> dict[str, Any]:
    contract = load_json_file(SEAL_CONTRACT_PATH, {})
    policy = contract.get("manual_pack_selection_policy", {}) if isinstance(contract, dict) else {}
    return policy if isinstance(policy, dict) else {}


def _sample_target_files(samples: list[dict[str, Any]]) -> list[str]:
    targets: list[str] = []
    for sample in samples:
        body = str(sample.get("body") or "")
        for match in re.finditer(r"^\s*target_file:\s*\"?([^\"\n]+)\"?\s*$", body, re.MULTILINE):
            target = match.group(1).replace("\\", "/").strip("/")
            if target and target not in targets:
                targets.append(target)
    return targets


def _pick_target_file(target_root: str = "") -> str:
    atlas = load_atlas_data(_raw_dir_for_review(target_root))
    project_items = list((atlas or {}).items())
    project_items.sort(key=lambda item: (0 if str(item[0]).upper() == "MAIN" else 1, str(item[0])))
    candidates: list[tuple[int, str]] = []
    for _project, payload in project_items:
        if not isinstance(payload, dict):
            continue
        files = payload.get("files")
        if not isinstance(files, dict):
            continue
        for file_key, info in files.items():
            rel = str((info or {}).get("workspace_rel") or file_key if isinstance(info, dict) else file_key)
            rel = rel.replace("\\", "/").strip().strip("/")
            rel_parts = [part.lower() for part in rel.split("/") if part]
            if not target_root and rel_parts and rel_parts[0] in {"variations", "companions", "corpus"}:
                continue
            if not rel.lower().endswith((".tsx", ".ts", ".jsx", ".js", ".py")):
                continue
            if rel.endswith("__init__.py") or rel.endswith("/__init__.py"):
                continue
            if not isinstance(info, dict):
                candidates.append((1, rel))
                continue
            score = (
                len(info.get("symbols") or []) * 3
                + len(info.get("features") or []) * 2
                + len(info.get("internal_deps") or [])
                + len(info.get("lazy_internal_deps") or [])
            )
            if score > 0:
                candidates.append((score, rel))
    if candidates:
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][1]
    return "src/App.tsx"


def _query_from_target(target_file: str) -> str:
    stem = Path(target_file).stem
    return stem if stem else "App"


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def _first_work_queue_target(body: str) -> dict[str, str]:
    item: dict[str, str] = {}
    in_first_item = False
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("- id:"):
            if in_first_item:
                break
            in_first_item = True
            item["id"] = _yaml_scalar(line.split(": ", 1)[1]) if ": " in line else ""
            continue
        if not in_first_item or ": " not in line:
            continue
        key, value = line.split(": ", 1)
        if key in {"target_file", "target_ref", "rule"}:
            item[key] = _yaml_scalar(value)
    return item


def _is_bounded_clean_sage_audit_queue_brief(body: str) -> bool:
    """Accept an empty queue only when its bounded authority is explicit."""

    required_markers = (
        'status: "clean_within_sage_audit"',
        "total_violations: 0",
        "items:\n  []",
        "coverage:",
        'status: "partial"',
        'sage_audit: "evaluated"',
        'target_native: "not_evaluated"',
        'combined_verdict: "not_available"',
        'clean_scope: "sage_audit_only"',
        "authority_projection:",
        'actionability: "no_action"',
        "mutation_proposed: false",
        'mutation_authority: "not_granted_by_this_directive"',
        "Target-native enforcement was not evaluated",
        "Do not patch repository code from an empty work queue.",
        "Do not claim repository-wide cleanliness or target-native compliance",
    )
    return all(marker in body for marker in required_markers)


def _yaml_field(body: str, field: str) -> str:
    prefix = f"{field}: "
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            return _yaml_scalar(line.split(": ", 1)[1])
    return ""


def _yaml_list_under(body: str, field: str) -> list[str]:
    values: list[str] = []
    collecting = False
    base_indent = 0
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped == f"{field}:":
            collecting = True
            base_indent = len(raw_line) - len(raw_line.lstrip())
            continue
        if not collecting:
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        if stripped.startswith("- "):
            values.append(_yaml_scalar(stripped[2:]))
            continue
        if stripped and indent <= base_indent:
            break
    return values


def _first_yaml_object_field_under(body: str, section: str, field: str) -> str:
    collecting = False
    base_indent = 0
    prefixes = (f"- {field}: ", f"{field}: ")
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped == f"{section}:":
            collecting = True
            base_indent = len(raw_line) - len(raw_line.lstrip())
            continue
        if not collecting:
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        if stripped and indent <= base_indent:
            break
        if stripped.startswith(prefixes):
            return _yaml_scalar(stripped.split(": ", 1)[1])
    return ""


def _yaml_file_fields(body: str) -> list[str]:
    values: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith(("- file: ", "file: ")):
            continue
        _, value = line.split(": ", 1)
        value = _yaml_scalar(value).replace("\\", "/").strip("/")
        if value and value not in values:
            values.append(value)
    return values


def _openable_yaml_files(body: str) -> list[str]:
    analysis_root = _yaml_field(body, "analysis_root")
    if not analysis_root:
        return []
    root = Path(analysis_root)
    return [path for path in _yaml_file_fields(body) if (root / path).exists()]


def _log(message: str) -> None:
    print(f"[AGENT_SURFACE] {message}", file=sys.stderr, flush=True)


def _utf8_clean(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return REPLACEMENT_CHARACTER not in text and not has_suspicious_text(text)


def _sample(name: str, producer: Callable[[], str]) -> dict[str, Any]:
    _log(f"sampling {name}")
    try:
        body = producer()
    except Exception as exc:
        body = f"[exception] {exc}"
    estimated_tokens = estimate_tokens(body)
    _log(f"sampled {name}: {len(body)} chars, ~{estimated_tokens} tokens")
    return {
        "name": name,
        "chars": len(body),
        "estimated_tokens": estimated_tokens,
        "token_budget_class": token_budget_class(estimated_tokens),
        "body": body,
        "excerpt": body[:1800],
    }


def _sample_missing_watchdog_session() -> str:
    original_raw_dir = mcp_server.RAW_DIR
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            mcp_server.RAW_DIR = Path(temp_dir)
            return mcp_server.get_watchdog_session()
        finally:
            mcp_server.RAW_DIR = original_raw_dir


def _inapplicable_exact_patch_probe(target_file: str) -> str:
    return "\n".join(
        [
            f"--- a/{target_file}",
            f"+++ b/{target_file}",
            "@@ -1,1 +1,2 @@",
            " this context line intentionally does not exist",
            "+import x from '../outside';",
            "",
        ]
    )


def _sample_actor_proposal_conformance(target_file: str, target_root: str) -> str:
    repository_scope = target_root or "analyzed_repository"
    request = {
        "request_id": "quality-review-proposal",
        "principal_id": "quality-review",
        "actor_id": "tool:agent-surface-quality-review",
        "actor_type": "tool_actor",
        "adapter_type": "mcp",
        "purpose": "Propose one bounded no-behavior-change refactor for quality review.",
        "operation": "propose",
        "target_scope": {"repository": repository_scope, "files": [target_file]},
        "requested_mode": "propose",
        "repository_snapshot": "quality-review-current-analysis-reference",
        "constraints": ["no behavior change", "no authority escalation"],
        "expected_outcome": "One bounded proposal awaiting independent validation and authorization.",
    }
    proposal = {
        "proposal_id": "quality-review-proposal-1",
        "request_id": request["request_id"],
        "actor_id": request["actor_id"],
        "purpose": request["purpose"],
        "repository_snapshot": request["repository_snapshot"],
        "affected_scope": request["target_scope"],
        "intended_changes": ["Consolidate duplicated internal behavior within the declared file."],
        "expected_effects": ["Preserve externally observable behavior."],
        "known_risks": [],
        "required_validations": ["source_contract_validation", "regression_suite"],
        "evidence_used": ["agent_surface_quality_review"],
        "interaction_state": "PROPOSED",
    }
    return asyncio.run(
        mcp_server.dispatch_actor_request(
            request,
            "validate_actor_proposal",
            {"proposal": proposal},
        )
    )


def _agent_eye_lifecycle(review: dict[str, Any]) -> dict[str, Any]:
    passed = bool(review.get("passed"))
    lifecycle = dict(_quality_review_contract().get("required_lifecycle_markers", {}))
    lifecycle.update({
        "automated_agent_eye_check": "passed" if passed else "failed",
        "rule": "Automated and agent-eye checks prepare evidence; final release seal requires explicit Progressive HITL approval.",
    })
    return lifecycle


def _review_sample(sample: dict[str, Any], *, require_yaml: bool = True, require_action: bool = True) -> dict[str, Any]:
    body = str(sample.get("body") or "")
    yaml_fence_count = body.count("```yaml")
    fence_count = body.count("```")
    encoding_markers = [marker for marker in SUSPICIOUS_MARKERS if marker and marker in body]
    has_encoding_risk = REPLACEMENT_CHARACTER in body or has_suspicious_text(body)
    checks = {
        "has_markdown_title": body.startswith("# "),
        "has_single_yaml_block": yaml_fence_count == 1 if require_yaml else True,
        "has_closed_fenced_block": fence_count >= 2 and fence_count % 2 == 0 if require_yaml else True,
        "has_action_language": ("do:" in body or "next_step:" in body) if require_action else True,
        "has_scope_guard": ("do_not:" in body or "Do not" in body),
        "hides_internal_default_markers": not any(marker in body for marker in _forbidden_default_markers()),
        "has_utf8_clean_agent_text": not has_encoding_risk,
        "bounded": len(body) < 12000,
        "token_budget_is_bounded": estimate_tokens(body) <= BOUNDED_AGENT_PACKET_TOKENS,
    }
    if "analysis_root +" in body:
        checks["path_contract_defines_analysis_root"] = "analysis_root:" in body or '"analysis_root"' in body
    return {
        "name": sample.get("name"),
        "passed": all(checks.values()),
        "checks": checks,
        "encoding_markers": encoding_markers,
    }


def _review_named_sample(sample: dict[str, Any], *, target_root: str = "") -> dict[str, Any]:
    review = _review_sample(sample)
    body = str(sample.get("body") or "")
    name = str(sample.get("name") or "")
    if name == "actor_proposal_conformance":
        review = _review_sample(sample, require_yaml=False, require_action=False)
        try:
            gateway = json.loads(body)
            tool_result = json.loads(str(gateway.get("tool_result") or "{}"))
        except (TypeError, ValueError):
            gateway = {}
            tool_result = {}
        completed_states = gateway.get("completed_states") if isinstance(gateway.get("completed_states"), list) else []
        claim_boundary = str(tool_result.get("claim_boundary") or "")
        review["checks"]["has_markdown_title"] = True
        review["checks"]["has_scope_guard"] = (
            tool_result.get("authorization_granted") is False
            and tool_result.get("validation_completed") is False
        )
        review["checks"]["proposal_gateway_preserves_non_authorizing_terminal"] = (
            gateway.get("status") == "PROPOSAL_CONFORMANT"
            and completed_states
            and completed_states[-1] == "PROPOSED"
            and "AUTHORIZED" not in completed_states
            and "ACCEPTED" not in completed_states
        )
        review["checks"]["proposal_tool_result_denies_authority_and_validation"] = (
            tool_result.get("status") == "PROPOSAL_CONFORMANT"
            and tool_result.get("authorization_granted") is False
            and tool_result.get("validation_completed") is False
        )
        review["checks"]["proposal_claim_boundary_is_explicit"] = (
            "does not prove technical correctness" in claim_boundary
            and "authorize execution" in claim_boundary
        )
        review["passed"] = all(review["checks"].values())
    if name == "operator_packet":
        review = _review_sample(sample, require_yaml=True, require_action=True)
        review["checks"]["operator_packet_default_is_target_agent_projection"] = (
            "# Operator Packet Brief" in body
            and 'surface: "target_repository_coding_agent"' in body
            and "contains_platform_status: false" in body
            and "directives:" in body
            and "mission_control" not in body
            and "release_readiness" not in body
            and "source_artifacts" not in body
            and "atlas_node" not in body
            and "file_context" not in body
        )
        first_target = ""
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if line.startswith("target_files: "):
                try:
                    values = json.loads(line.split(": ", 1)[1])
                    if isinstance(values, list) and values:
                        first_target = str(values[0])
                except Exception:
                    first_target = ""
                break
        analysis_root = _yaml_field(body, "analysis_root")
        review["checks"]["operator_packet_first_target_is_openable"] = (
            not first_target
            or (bool(analysis_root) and (Path(str(analysis_root)) / str(first_target)).exists())
        )
        review["checks"]["operator_packet_default_target_scope_is_main"] = (
            not first_target
            or not str(first_target).replace("\\", "/").startswith("Variations/")
        )
        review["passed"] = all(review["checks"].values())
    if name == "inspect_file" and "architecture_violation" in body:
        review["checks"]["inspect_findings_have_rule_guidance"] = (
            "why_it_matters:" in body and "fix_strategy:" in body and "target_ref:" in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "search_symbols":
        review["checks"]["search_results_point_to_inspection"] = (
            "target_ref:" in body and "next_tool:" in body and "Do not edit from search results alone." in body
        )
        if 'query: "main"' in body:
            review["checks"]["search_symbols_prioritizes_exact_file_match"] = (
                'name: "main.tsx"' in body
                and 'file: "src/main.tsx"' in body
                and 'target_ref: "MAIN::src/main.tsx"' in body
                and 'match_quality: "exact_or_token"' in body
                and 'next_tool: "inspect_file(file_path=\\"MAIN::src/main.tsx\\")"' in body
            )
        else:
            review["checks"]["search_symbols_returns_actionable_target_refs"] = (
                "file:" in body
                and 'target_ref: "MAIN::' in body
                and "open_files_with:" in body
            )
        review["passed"] = all(review["checks"].values())
    if name == "external_target_preflight_invalid":
        review["checks"]["external_target_preflight_invalid_is_structured_fail_closed"] = (
            "# Invalid External Target" in body
            and "status: invalid_external_target_root" in body
            and "external_target_preflight(target_root=...)" in body
            and "Do not fall back to the current SAGE workspace" in body
            and "```yaml" in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "test_impact":
        has_impacted_test_item = "impacted_test_count: 0" not in body and "file:" in body and "run:" in body
        has_empty_result_fallback = "impacted_test_count: 0" in body and "commands:\n    []" not in body
        review["checks"]["test_impact_has_directive_and_empty_list_guard"] = (
            "directive:" in body
            and "next_action:" in body
            and "confidence_note:" in body
            and "target_grounding_status:" in body
            and "Do not treat an empty test-impact list as proof" in body
        )
        review["checks"]["test_impact_agent_eye_can_run_or_inspect_tests"] = (
            "validation:" in body
            and "commands:" in body
            and "command_contract_summary:" in body
            and "projection:" in body
            and "command_count:" in body
            and "example_commands:" in body
            and "impacted_tests:" in body
            and (has_impacted_test_item or has_empty_result_fallback)
            and "source_snippets_shown:" in body
            and "test_source_snippets_attached:" in body
            and "test_source_snippets_omitted:" in body
            and "Do not grep the whole test tree before using listed source_snippets and run commands." in body
        )
        if "impacted_test_count: 0" in body:
            review["checks"]["test_impact_empty_list_has_validation_fallback"] = (
                "validation:" in body
                and "commands:" in body
                and "commands:\n    []" not in body
            )
        if 'target_file: "src/main.tsx"' in body:
            review["checks"]["test_impact_target_ref_preserves_repo_relative_path"] = (
                'target_ref: "MAIN::src/main.tsx"' in body
                and 'target_ref: "MAIN::main.tsx"' not in body
            )
        review["passed"] = all(review["checks"].values())
    if name == "test_impact_filled":
        review["checks"]["test_impact_filled_has_openable_test_path"] = (
            'file: "src/shared/utils/text.test.ts"' in body
            and 'run: "pnpm test src/shared/utils/text.test.ts"' in body
            and 'pnpm test shared/utils/text.test.ts' not in body
            and 'target_file: "src/shared/utils/text.ts"' in body
            and 'target_ref: "MAIN::src/shared/utils/text.ts"' in body
        )
        review["checks"]["test_impact_filled_has_operational_next_action"] = (
            "run_listed_tests_before_finalizing" in body
            and "open_files_with:" in body
            and "analysis_root + target_file or impacted_tests.file" in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "patch_validation":
        review["checks"]["patch_validation_has_apply_safety_decision"] = (
            "safe_to_apply:" in body
            and "directive:" in body
            and "next_action:" in body
            and "Do not treat PASS as permission for unrelated refactors." in body
        )
        review["checks"]["patch_validation_empty_patch_is_noop_not_apply"] = (
            'status: "NO_OP"' in body
            and "safe_to_apply: false" in body
            and "no_op_patch: true" in body
            and "provide_non_empty_patch_before_validation" in body
            and "instruction:" in body
            and "Do not treat NO_OP as approval" in body
            and "agent_rule:" in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "patch_validation_negative":
        review["checks"]["patch_validation_negative_blocks_bad_patch"] = (
            'status: "FAIL"' in body
            and "safe_to_apply: false" in body
            and 'application_readiness: "NOT_APPLICABLE"' in body
            and 'patch_applicability:\n  status: "FAIL"' in body
            and "exact_patch_not_applicable" in body
            and (
                "repair_exact_patch_payload_before_apply" in body
                or "provide_valid_patch_before_validation" in body
            )
            and "full_replacement_patch: false" in body
            and "human_approval_required: false" in body
            and "instruction:" in body
            and (
                "Do not apply this patch" in body
                or "Do not edit files from this result" in body
            )
            and "agent_rule:" in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "patch_validation_missing_target":
        review["checks"]["patch_validation_missing_target_blocks_ungrounded_patch"] = (
            'status: "FAIL"' in body
            and "safe_to_apply: false" in body
            and "target_exists: false" in body
            and "target_indexed: false" in body
            and "mcp_target_not_grounded" in body
            and "explicit create-file workflow with human approval" in body
            and "instruction:" in body
            and "Do not apply or inspect this missing target" in body
            and "agent_rule:" in body
        )
        review["checks"]["patch_validation_missing_target_does_not_request_impossible_inspection"] = (
            'open_files_with: "not_available_target_missing_or_unindexed"' in body
            and 'next_action: "refresh_target_analysis_or_request_create_file_approval"' in body
            and "inspect_first:\n    []" in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "surgical_operation_packet":
        review["checks"]["surgical_packet_has_target_repo_validation_tools"] = (
            "validation:" in body
            and "tools:" in body
            and "commands:" in body
            and "completion_rule:" in body
            and "target_files:" in body
            and "target_refs:" in body
            and "inspect_first:" in body
            and "path_contract:" in body
            and "validate_patch(target_file, patch_content)" in body
            and "get_test_impact(target_file)" in body
            and "validate_doctrine_audit_quality_integrity.py" not in body
            and "validate_contextos_contracts.py" not in body
            and "Do not invent cleanup work" in body
            and "debug_internal_refs:" not in body
        )
        review["checks"]["surgical_packet_default_target_scope_is_main"] = (
            'target_project: MAIN' in body
            or (
                "target_projects:" in body
                and "- MAIN" in body
                and "Variations/" not in body.split("target_files:", 1)[1].split("target_refs:", 1)[0]
            )
        )
        review["passed"] = all(review["checks"].values())
    if name == "confidence_score":
        review["checks"]["confidence_brief_uses_aggregate_risk_without_flattening_dimensions"] = (
            "aggregate_risk:" in body
            and "Treat merge_safety as the dependency-blast dimension only" in body
            and "SAFE cannot override architecture" in body
            and "If aggregate_risk is MEDIUM or HIGH" in body
            and "decision_boundary:" in body
            and "not a standalone merge or deploy approval" in body
            and "target_grounding_status:" in body
            and "auto-deploy" not in body
            and "small_patch_allowed_after_file_inspection" not in body
        )
        if 'target_file: "src/main.tsx"' in body:
            review["checks"]["confidence_target_ref_preserves_repo_relative_path"] = (
                'target_ref: "MAIN::src/main.tsx"' in body
                and 'target_ref: "src/main.tsx"' not in body
            )
        review["passed"] = all(review["checks"].values())
    if name == "confidence_missing_target":
        review["checks"]["confidence_missing_target_is_not_safe"] = (
            'target_exists: false' in body
            and 'target_indexed: false' in body
            and 'target_grounding_status: "missing_or_unindexed"' in body
            and 'merge_safety: "UNKNOWN_TARGET_NOT_GROUNDED"' in body
            and "architecture_drift_certainty: null" in body
            and "dead_code_confidence: null" in body
            and "refresh_target_analysis_before_confidence_decision" in body
        )
        review["checks"]["confidence_missing_target_does_not_request_impossible_inspection"] = (
            'open_files_with: "not_available_target_missing_or_unindexed"' in body
            and "inspect_first:\n    []" in body
        )
        review["passed"] = all(review["checks"].values())
    if name in {"impact_radius", "work_queue_roundtrip_impact"}:
        review["checks"]["impact_radius_is_depth_limited_and_path_safe"] = (
            "radius_depth:" in body
            and "returned_scope_size:" in body
            and "target_grounding_status:" in body
            and "direct_dependents_omitted:" in body
            and "transitive_dependents_omitted:" in body
            and "depth-limited bounded sample" in body
            and "follow_up:" in body
            and "when_to_use:" in body
            and "depth=3" in body
            and "depth=0" in body
            and (
                "transitive_dependents_omitted: 0" in body
                or (
                    "Use depth=3 first" in body
                    and "depth=0 full graph only as a last resort" in body
                )
            )
            and "target_ref:" in body
            and "MAIN::main.tsx" not in body
        )
        review["checks"]["impact_radius_agent_eye_gets_files_not_counts_only"] = (
            "blast_radius_size:" in body
            and "direct_dependents:" in body
            and "file:" in body
            and "target_ref:" in body
            and "transitive_dependents_sample:" in body
            and "direct_dependents_omitted:" in body
            and "transitive_dependents_omitted:" in body
            and "Do not expand work to omitted dependents from this brief alone." in body
        )
        review["checks"]["impact_radius_lists_openable_dependents_when_present"] = (
            "direct_dependents:" in body
            and ("direct_dependents_count: 0" in body or bool(_openable_yaml_files(body)))
        )
        if name == "work_queue_roundtrip_impact":
            target_file = _yaml_field(body, "target_file")
            first_direct_dependent = _first_yaml_object_field_under(body, "direct_dependents", "file")
            inspect_first = _yaml_list_under(body, "inspect_first")
            review["checks"]["impact_directive_lists_first_direct_dependent"] = bool(
                target_file
                and target_file in inspect_first
                and (
                    not first_direct_dependent
                    or first_direct_dependent in inspect_first
                )
            )
        review["passed"] = all(review["checks"].values())
    if name == "upstream_trace":
        target_file = _yaml_field(body, "target_file")
        target_ref = _yaml_field(body, "target_ref")
        review["checks"]["upstream_trace_preserves_target_ref"] = (
            bool(target_file)
            and bool(target_ref)
            and "::" in target_ref
            and target_ref.endswith(target_file)
            and not target_ref.startswith(target_file)
            and "target_grounding_status:" in body
        )
        review["checks"]["upstream_trace_is_operational_or_fail_closed"] = (
            "target_grounding_status: \"missing_or_unindexed\"" in body
            or (
                "directive:" in body
                and "inspect_first:" in body
                and "follow_up:" in body
                and "Do not edit upstream files unless the code confirms causality." in body
            )
        )
        review["checks"]["upstream_trace_agent_eye_has_causal_evidence_line"] = (
            "upstream_candidates:" in body
            and "dependency_evidence_snippets:" in body
            and "source_snippets:" in body
            and "matched_line:" in body
            and "included_dependency_evidence_line" in body
            and " imports " in body
            and "Do not edit upstream files unless the code confirms causality." in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "upstream_trace_filled":
        target_file = _yaml_field(body, "target_file")
        target_ref = _yaml_field(body, "target_ref")
        review["checks"]["upstream_trace_exposes_real_graph_context"] = (
            "upstream_dependency_count:" in body
            and "direct_dependent_count:" in body
            and "direct_dependents_sample:" in body
            and (
                "inspect_upstream_candidates_before_local_patch" in body
                or 'next_action: "patch_target_only_after_local_evidence"' in body
            )
            and "Do not edit upstream files unless the code confirms causality." in body
        )
        review["checks"]["upstream_trace_uses_canonical_target_ref"] = (
            bool(target_file)
            and bool(target_ref)
            and "::" in target_ref
            and target_ref.endswith(target_file)
            and f"get_impact_radius(target_node='{target_ref}', depth=2)" in body
        )
        review["checks"]["upstream_trace_candidates_are_openable_from_analysis_root"] = (
            "target_grounding_status: \"grounded\"" in body
            and bool(_openable_yaml_files(body))
            and (
                len(_openable_yaml_files(body)) >= 3
                or "upstream_candidates:" in body
                or "direct_dependents_sample:" in body
            )
        )
        review["passed"] = all(review["checks"].values())
    if name == "upstream_trace_path_resolution":
        target_file = _yaml_field(body, "target_file")
        target_ref = _yaml_field(body, "target_ref")
        review["checks"]["upstream_trace_resolves_workspace_path_to_graph_node"] = (
            'target_project: "MAIN"' in body
            and bool(target_file)
            and target_file.startswith("src/")
            and "/" in target_file
            and target_ref == f"MAIN::{target_file}"
            and "direct_dependent_count:" in body
            and 'target_grounding_status: "grounded"' in body
            and bool(_openable_yaml_files(body))
        )
        review["passed"] = all(review["checks"].values())
    if name == "state_flow":
        if "status: missing_required_target_artifacts" in body:
            review["checks"]["state_flow_missing_artifact_is_fail_closed"] = (
                "Do not infer this result from another repository or stale workspace artifacts." in body
            )
        else:
            review["checks"]["state_flow_exposes_openable_sample_targets"] = (
                "sample_targets" in body
                and "target_file" in body
                and "target_ref" in body
                and "sample_keys" in body
                and "sample_keys_omitted" in body
                and "sample_keys are graph references, not filesystem paths" in body
                and "sample_targets_omitted" in body
                and "max_items limits returned top-level state-flow sections" in body
                and "changing state shape, selectors, or mutation paths" in body
                and "target_abs" not in body
                and "resolved_node" not in body
            )
            review["checks"]["state_flow_default_scope_is_main_not_variations"] = (
                'filter: "MAIN"' in body
                and "MAIN::" in body
                and "Variations/" not in body
            )
        review["passed"] = all(review["checks"].values())
    if name == "ui_architecture":
        if "status: missing_required_target_artifacts" in body:
            review["checks"]["ui_architecture_missing_artifact_is_fail_closed"] = (
                "Do not infer this result from another repository or stale workspace artifacts." in body
            )
        elif "items:\n  []" in body:
            review["checks"]["ui_architecture_empty_result_is_explicit"] = (
                "returned: 0" in body
                and "Do not assume missing items means the repository has no risk" in body
                and "Treat this as context, not as permission to broaden the patch." in body
            )
        else:
            review["checks"]["ui_architecture_exposes_openable_target_paths"] = (
                "target_file" in body
                and "target_ref" in body
                and "target_status" in body
                and "changing component boundaries, exports, or UI ownership" in body
                and "confirm the actual code before editing" in body
                and "target_abs" not in body
                and "resolved_node" not in body
            )
        review["passed"] = all(review["checks"].values())
    if name == "dead_code":
        if "status: missing_required_target_artifacts" in body:
            review["checks"]["dead_code_missing_artifact_is_fail_closed"] = (
                "Do not infer this result from another repository or stale workspace artifacts." in body
            )
        elif "no_actionable_items" in body:
            review["checks"]["dead_code_empty_result_is_explicit"] = (
                "items:\n  []" in body
                and "Do not assume missing items means the repository has no risk" in body
            )
        else:
            review["checks"]["dead_code_exposes_openable_target_paths"] = (
                "target_file" in body
                and "target_ref" in body
                and "target_status" in body
                and "public export, package entry point, barrel export, type augmentation" in body
                and "unusedness confidence and remediation intent as separate decisions" in body
                and '"intent_decision_required": true' in body
                and '"mutation_proposed": false' in body
                and "target_abs" not in body
                and "resolved_node" not in body
            )
        review["passed"] = all(review["checks"].values())
    if name == "circular_dependencies":
        if "status: missing_required_target_artifacts" in body:
            review["checks"]["circular_missing_artifact_is_fail_closed"] = (
                "Do not infer this result from another repository or stale workspace artifacts." in body
            )
        elif "no_actionable_items" in body:
            review["checks"]["circular_empty_result_is_explicit"] = (
                "items:\n  []" in body
                and "Do not assume missing items means the repository has no risk" in body
            )
        else:
            review["checks"]["circular_exposes_openable_chain_targets"] = (
                "chain_targets" in body
                and "chain_targets_omitted" in body
                and "target_file" in body
                and "target_ref" in body
                and "smallest dependency edge that can break the cycle" in body
                and "Prefer moving a type/port/contract boundary" in body
                and "target_abs" not in body
                and "resolved_node" not in body
            )
        review["passed"] = all(review["checks"].values())
    if name == "blast_radius_supporting":
        review["checks"]["blast_radius_supporting_is_compact_and_openable"] = (
            "surface: \"blast_radius\"" in body
            and "target_file" in body
            and "target_ref" in body
            and "target_status" in body
            and "imported_contracts_sample" in body
            and "imported_contracts_omitted" in body
            and "member_dependencies_sample" in body
            and "member_dependencies_omitted" in body
            and "Inspect target_file first" in body
            and "target_abs" not in body
            and "resolved_node" not in body
            and "member_dependencies\": [\"AnalysisReport\"" not in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "hexagonal_bindings":
        review["checks"]["hexagonal_bindings_exposes_openable_targets"] = (
            "surface: \"hexagonal_bindings\"" in body
            and "port_target" in body
            and "adapter_targets" in body
            and "target_file" in body
            and "target_ref" in body
            and "target_status" in body
            and "Inspect port_target first" in body
            and '"port_file": "MAIN::' not in body
            and '"file": "MAIN::' not in body
            and "target_abs" not in body
            and "resolved_node" not in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "health_metrics":
        review["checks"]["health_metrics_is_triage_not_edit_directive"] = (
            "surface: \"health_metrics\"" in body
            and "Use this bounded triage context before selecting a concrete edit target." in body
            and "No direct edit target; first request a concrete target_file from a primary agent tool." in body
            and "Use this as triage only" in body
            and "do not infer a file-level fix from the score alone" in body
            and "target_abs" not in body
            and "resolved_node" not in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "module_integrity":
        review["checks"]["module_integrity_exposes_openable_targets"] = (
            "# Module Integrity Brief" in body
            and "analysis_root:" in body
            and "path_contract:" in body
            and (
                ('status: "healthy"' in body and "returned: 0" in body and "items:\n  []" in body)
                or (
                    "target_file:" in body
                    and "target_ref:" in body
                    and "target_status:" in body
                    and "inspect_first:" in body
                )
            )
            and "SAGE/MCP reference only; not a filesystem path" in body
            and "inspect_first: [\"EXAMPLE_VARIANT_" not in body
            and "target_abs" not in body
            and "resolved_node" not in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "surgical_context":
        review["checks"]["surgical_context_exposes_openable_symbol_target"] = (
            "surface: \"surgical_context\"" in body
            and "target_file" in body
            and "target_ref" in body
            and "target_status" in body
            and "\"file\": \"src/" not in body
            and "target_abs" not in body
            and "resolved_node" not in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "clone_context":
        review["checks"]["clone_context_exposes_openable_instance_targets"] = (
            "surface: \"clone_detector\"" in body
            and "instance_targets" in body
            and "target_file" in body
            and "target_ref" in body
            and "target_status" in body
            and "\"instances\": [\"LINGUASCRIBE_MASTER::" not in body
            and "target_abs" not in body
            and "resolved_node" not in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "inspect_folder":
        review["checks"]["inspect_folder_source_grounding_is_consistent"] = (
            "# Target Inspection Brief" in body
            and "target_kind: \"folder\"" in body
            and "target_files:" in body
            and "target_refs:" in body
            and "source_grounding:" in body
            and "missing_target_files: 0" in body
            and "missing_target_files_sample:" not in body
            and "target_refs_usage: \"SAGE/MCP follow-up references only; not filesystem paths\"" in body
            and "target_abs" not in body
            and "resolved_node" not in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "watchdog_session_missing":
        missing_brief = (
            "# Watchdog Session Missing" in body
            and "status: missing_watchdog_session" in body
            and "run_watchdog_once(path?)" in body
            and "<file-or-directory>" in body
            and "Do not infer that the repository has no active risk" in body
            and "Do not fall back to stale active-signal or unrelated target artifacts." in body
        )
        review["checks"]["watchdog_missing_is_structured_fail_closed"] = (
            missing_brief and "```yaml" in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "simulate_change_impact":
        review["checks"]["simulate_change_impact_uses_bounded_impact_brief"] = (
            "# Impact Radius Brief" in body
            and "radius_depth: 2" in body
            and "direct_dependents_omitted:" in body
            and "transitive_dependents_omitted:" in body
            and "deeper_scope:" in body
            and "full_graph:" in body
            and "Do not expand work to omitted dependents from this brief alone." in body
            and "target_abs" not in body
            and "resolved_node" not in body
        )
        review["passed"] = all(review["checks"].values())
    if name == "violation_work_queue":
        review["checks"]["work_queue_is_operational_not_internal_accounting"] = (
            "returned_work_items:" in body
            and "raw_page_items:" in body
            and "grouped_work_items:" in body
            and "omitted_work_items:" in body
            and "raw_page_items_grouped:" in body
            and "grouping_policy:" in body
            and "projection_policy:" in body
            and "raw_returned:" not in body
            and "evidence_status:" not in body
        )
        if 'status: "fail"' in body and "refresh_sage_evidence_before_editing" in body:
            review["checks"]["work_queue_fail_closed_on_stale_evidence"] = (
                "items:\n  []" in body
                and "Do not edit target repository code from stale or incomplete queue evidence." in body
            )
            review["passed"] = all(review["checks"].values())
            return review
        clean_queue = _is_bounded_clean_sage_audit_queue_brief(body)
        target = _first_work_queue_target(body)
        target_file = target.get("target_file") or ""
        target_ref = target.get("target_ref") or ""
        review["checks"]["work_queue_first_item_has_dual_path_contract"] = (
            clean_queue
            or (
                bool(target_file)
                and bool(target_ref)
                and "::" in target_ref
                and target_ref.endswith(target_file)
            )
        )
        review["checks"]["work_queue_first_item_is_openable"] = (
            clean_queue
            or (
                bool(target_file)
                and (_analysis_root_for_review(target_root) / target_file).exists()
            )
        )
        if name == "violation_work_queue":
            review["checks"]["default_work_queue_targets_main_not_variations"] = (
                clean_queue
                or (
                    target_ref.startswith("MAIN::")
                    and not target_file.replace("\\", "/").startswith("Variations/")
                )
            )
        review["passed"] = all(review["checks"].values())
    if name in {"domain_ui_work_queue", "domain_ui_all_projects_work_queue"}:
        invalid_context = is_structured_precondition_block(
            sample,
            expected_tool="get_violation_work_queue",
        )
        stale_fail_closed = (
            'status: "fail"' in body
            and "refresh_sage_evidence_before_editing" in body
            and "items:\n  []" in body
            and "Do not edit target repository code from stale or incomplete queue evidence." in body
        )
        clean_queue = _is_bounded_clean_sage_audit_queue_brief(body)
        if invalid_context:
            # A canonical lifecycle precondition block is a complete alternate
            # response shape; it is not a queue brief with an embedded YAML
            # payload. Its structured fields are validated by the shared
            # classifier above.
            review["checks"]["has_single_yaml_block"] = True
            review["checks"]["has_closed_fenced_block"] = True
            review["checks"]["has_action_language"] = True
            review["checks"]["has_scope_guard"] = True
        review["checks"]["domain_ui_import_target_is_extension_resolved"] = (
            clean_queue
            or stale_fail_closed
            or invalid_context
            or "types/domain/stores.ts imports ../ui" not in body
            or "types/ui.ts" in body
        )
        review["checks"]["domain_ui_enforced_rule_requires_human_approval"] = (
            clean_queue
            or stale_fail_closed
            or invalid_context
            or (
                'rule: "domain_ui_leaks"' in body
                and "human_approval_required: true" in body
            )
        )
        review["passed"] = all(review["checks"].values())
    if name == "inspect_symbol":
        target_files = _yaml_list_under(body, "target_files")
        target_refs = _yaml_list_under(body, "target_refs")
        analysis_root = _yaml_field(body, "analysis_root")
        openable_targets = [
            item for item in target_files
            if item and analysis_root and (Path(analysis_root) / item).exists()
        ]
        refs_are_main_scoped = bool(target_refs) and all(str(ref).startswith("MAIN::") for ref in target_refs)
        refs_do_not_leak_filesystem_paths = all(not str(ref).startswith("lifecycle-modules/") for ref in target_refs)
        review["checks"]["symbol_inspection_has_openable_workspace_paths_and_refs"] = (
            'target_kind: "symbol"' in body
            and "target_files:" in body
            and "target_refs:" in body
            and "returned_target_refs:" in body
            and "open_files_with:" in body
            and "target_refs_counting:" in body
            and (
                "findings[].target_file" in body
                or "target_files item" in body
            )
            and bool(target_files)
            and bool(target_refs)
            and refs_are_main_scoped
            and refs_do_not_leak_filesystem_paths
        )
        review["checks"]["symbol_inspection_targets_are_openable_from_analysis_root"] = (
            bool(target_files)
            and len(openable_targets) == len(target_files)
            and "source_grounding:" in body
            and (
                "source_status: \"openable_in_analysis_root\"" in body
                or "missing_target_files: 0" in body
            )
        )
        review["checks"]["symbol_inspection_is_fail_closed_when_ambiguous"] = (
            "returned_target_refs:" not in body
            or "returned_target_refs: 1" in body
            or (
                "ambiguity_note:" in body
                and "Choose exactly one target_ref/project scope" in body
                and "Do not patch while multiple target_refs are present." in body
            )
        )
        symbol_query = _yaml_field(body, "symbol_query")
        review["checks"]["symbol_one_shot_edit_requires_exact_symbol_match"] = (
            "one_shot_edit_ready: true" not in body
            or bool(symbol_query and f'symbol: "{symbol_query}"' in body)
        )
        review["passed"] = all(review["checks"].values())
    if name == "merge_review_queue":
        if "status: missing_required_target_artifacts" in body:
            review["checks"]["merge_review_queue_missing_artifact_is_fail_closed"] = (
                "Do not infer this result from another repository or stale workspace artifacts." in body
                and "Run or refresh analysis for this exact target_root before relying on this tool." in body
            )
        else:
            empty_review_only_queue = (
                "# Merge Review Queue" in body
                and "human_approval_required: true" in body
                and "mutation_allowed_by_this_packet: false" in body
                and "total_candidates: 0" in body
                and "items:\n  []" in body
                and "Review merge candidates without applying file copies or imports automatically." in body
                and "Do not treat Import Now as permission to mutate without human approval." in body
            )
            review["checks"]["merge_review_queue_is_bounded_and_human_gated"] = (
                empty_review_only_queue
                or (
                    "# Merge Review Queue" in body
                    and "human_approval_required: true" in body
                    and "mutation_allowed_by_this_packet: false" in body
                    and "source_file:" in body
                    and "source_file_role: \"read_only_evidence\"" in body
                    and "proposed_target_path:" in body
                    and "proposed_target_role: \"main_candidate_requires_human_approval\"" in body
                    and "source_file_role: \"read-only variation/source evidence\"" in body
                    and "proposed_target_path_role: \"MAIN target candidate; mutate only after human approval\"" in body
                    and "mode: \"review_only_until_human_approval\"" in body
                    and "pre_approval_tools:" in body
                    and "post_approval_tools:" in body
                    and "external_deps:" in body
                    and "Do not copy dependency packages automatically" in body
                    and "Do not edit source_file; it is variation/source evidence" in body
                    and "evidence_dependency_lists_are_not_open_file_targets: true" in body
                    and "Do not open evidence.external_deps as filesystem paths" in body
                    and "MCP follow-up calls inspect_file(target=source_ref)" in body
                    and "get_impact_radius(target_node=source_ref)" in body
                    and "get_test_impact(target_file=source_ref)" in body
                    and "target_abs" not in body
                    and "resolved_node" not in body
                )
            )
            review["checks"]["merge_review_queue_uses_target_agent_validation_policy"] = (
                empty_review_only_queue
                or (
                    "validate_patch(target_file=proposed_target_path, patch_content=bounded_patch)" in body
                    and "python -B .\\tools\\validate_" not in body
                    and "output/scripts/ui_smoke_specs" not in body
                )
            )
        review["passed"] = all(review["checks"].values())
    if name == "merge_review_queue_json":
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}
        payload_text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else body
        first_item = (payload.get("items") or [{}])[0] if isinstance(payload, dict) else {}
        source_status = first_item.get("source_file_status") if isinstance(first_item, dict) else {}
        target_status = first_item.get("proposed_target_status") if isinstance(first_item, dict) else {}
        inspect_first = first_item.get("inspect_first") if isinstance(first_item, dict) else []
        empty_review_only_queue = (
            isinstance(payload, dict)
            and payload.get("policy_boundary", {}).get("mutation_allowed_by_this_packet") is False
            and payload.get("policy_boundary", {}).get("human_approval_required") is True
            and int(payload.get("total_candidates") or 0) == 0
            and payload.get("items") == []
            and any("Do not treat Import Now as permission" in str(item) for item in payload.get("do_not", []))
        )
        target_proof = payload.get("target_proof") if isinstance(payload, dict) and isinstance(payload.get("target_proof"), dict) else {}
        blocked_evidence_queue = (
            isinstance(payload, dict)
            and payload.get("status") in {"BLOCKED", "INCOMPLETE_EVIDENCE"}
            and payload.get("policy_boundary", {}).get("mutation_allowed_by_this_packet") is False
            and payload.get("policy_boundary", {}).get("human_approval_required") is True
            and int(payload.get("total_candidates") or 0) == 0
            and payload.get("items") == []
            and target_proof.get("verdict") == "BLOCKED"
            and bool(target_proof.get("blocking_evidence"))
            and bool(str(payload.get("required_action") or "").strip())
            and "No merge candidate is served" in str(payload.get("claim_boundary") or "")
        )
        safe_empty_queue = empty_review_only_queue or blocked_evidence_queue
        review = _review_sample(sample, require_yaml=False, require_action=False)
        review["checks"]["has_markdown_title"] = True
        review["checks"]["has_scope_guard"] = safe_empty_queue or review["checks"]["has_scope_guard"]
        review["checks"]["merge_json_hides_internal_path_status"] = (
            safe_empty_queue
            or (
                isinstance(source_status, dict)
                and isinstance(target_status, dict)
                and "target_abs" not in payload_text
                and "resolved_node" not in payload_text
                and "target_ref" in source_status
                and "target_file" in source_status
                and "exists" in source_status
                and "indexed" in source_status
            )
        )
        review["checks"]["merge_json_has_same_operational_inspect_first_contract_as_brief"] = (
            safe_empty_queue
            or (
                isinstance(inspect_first, list)
                and first_item.get("source_file") in inspect_first
                and first_item.get("target_path") in inspect_first
            )
        )
        review["checks"]["merge_json_is_review_only_and_distinguishes_source_from_target"] = (
            safe_empty_queue
            or (
                payload.get("policy_boundary", {}).get("mutation_allowed_by_this_packet") is False
                and payload.get("policy_boundary", {}).get("human_approval_required") is True
                and any("Do not edit source_file" in str(item) for item in payload.get("do_not", []))
                and first_item.get("source_file")
                and first_item.get("target_path")
                and str(first_item.get("source_file")) != str(first_item.get("target_path"))
            )
        )
        validation = payload.get("validation", {}) if isinstance(payload, dict) else {}
        review["checks"]["merge_json_uses_target_agent_validation_policy"] = (
            safe_empty_queue
            or (
                validation.get("mode") == "review_only_until_human_approval"
                and bool(validation.get("pre_approval_tools"))
                and bool(validation.get("post_approval_tools"))
                and "python -B .\\tools\\validate_" not in payload_text
                and "output/scripts/ui_smoke_specs" not in payload_text
            )
        )
        review["passed"] = all(review["checks"].values())
    if name.startswith("work_queue_roundtrip_"):
        review["checks"]["roundtrip_preserves_work_queue_target_file"] = (
            ("target_file:" in body or "target_files:" in body)
            and "target_not_found_in_current_artifacts" not in body
            and ("target_ref:" in body or "target_refs:" in body)
        )
        review["passed"] = all(review["checks"].values())
    return review


def build_review(target_root: str = "") -> dict[str, Any]:
    _log("building review")
    target_file = _pick_target_file(target_root)
    query = _query_from_target(target_file)
    _log("sampling violation_work_queue")
    work_queue_body = mcp_server.get_violation_work_queue(page_size=5, target_root=target_root)
    _log(f"sampled violation_work_queue: {len(work_queue_body)} chars")
    work_queue_target = _first_work_queue_target(work_queue_body)
    roundtrip_target = work_queue_target.get("target_ref") or work_queue_target.get("target_file") or target_file
    upstream_trace_target = roundtrip_target if work_queue_target else target_file
    _log(f"target={target_file} query={query}")
    samples = [
        _sample(
            "actor_proposal_conformance",
            lambda: _sample_actor_proposal_conformance(target_file, target_root),
        ),
        _sample("operator_packet", lambda: mcp_server.get_operator_packet(target_root=target_root)),
        _sample("surgical_operation_packet", lambda: mcp_server.get_surgical_operation_packet(max_signals=3, target_root=target_root)),
        _sample("violation_work_queue", lambda: work_queue_body),
        _sample("merge_review_queue", lambda: mcp_server.get_merge_review_queue(max_items=2, target_root=target_root)),
        _sample("merge_review_queue_json", lambda: mcp_server.get_merge_review_queue(max_items=2, target_root=target_root, format="json")),
        _sample("state_flow", lambda: mcp_server.get_state_flow(max_items=5, target_root=target_root)),
        _sample("ui_architecture", lambda: mcp_server.get_ui_architecture(max_items=5, target_root=target_root)),
        _sample("dead_code", lambda: mcp_server.get_dead_code(max_items=5, target_root=target_root)),
        _sample("circular_dependencies", lambda: mcp_server.get_circular_dependencies(max_items=5, target_root=target_root)),
        _sample("blast_radius_supporting", lambda: mcp_server.get_blast_radius("Project", max_items=3, target_root=target_root)),
        _sample("hexagonal_bindings", lambda: mcp_server.get_hexagonal_bindings(max_items=3, target_root=target_root)),
        _sample("health_metrics", lambda: mcp_server.get_health_metrics(target_root=target_root)),
        _sample("module_integrity", lambda: mcp_server.check_module_integrity("src", max_items=3, target_root=target_root)),
        _sample("surgical_context", lambda: mcp_server.get_surgical_context("Project", target_root=target_root)),
        _sample("clone_context", lambda: mcp_server.find_clones("Project", max_items=3, target_root=target_root)),
        _sample("inspect_folder", lambda: mcp_server.inspect_folder("src/shared/types", target_root=target_root)),
        _sample("watchdog_session_missing", _sample_missing_watchdog_session),
        _sample("simulate_change_impact", lambda: mcp_server.simulate_change_impact("src/shared/types/project.ts", target_root=target_root)),
        _sample("work_queue_roundtrip_inspect", lambda: mcp_server.inspect_file(roundtrip_target, target_root=target_root)),
        _sample("work_queue_roundtrip_impact", lambda: mcp_server.get_impact_radius(roundtrip_target, target_root=target_root)),
        _sample("work_queue_roundtrip_test", lambda: mcp_server.get_test_impact(roundtrip_target, target_root=target_root)),
        _sample("work_queue_roundtrip_confidence", lambda: mcp_server.get_confidence_score(roundtrip_target, target_root=target_root)),
        _sample("work_queue_roundtrip_upstream", lambda: mcp_server.trace_upstream_cause(roundtrip_target, target_root=target_root)),
        _sample("active_signals", lambda: mcp_server.get_active_signals(max_files=2, max_chars_per_file=300, target_root=target_root)),
        _sample(
            "external_target_preflight_invalid",
            lambda: mcp_server.external_target_preflight(str(Path(tempfile.gettempdir()) / "__sage_missing_external_target__")),
        ),
        _sample("search_symbols", lambda: mcp_server.search_symbols(query, project="MAIN", target_root=target_root)),
        _sample("inspect_file", lambda: mcp_server.inspect_file(target_file, target_root=target_root)),
        _sample("inspect_symbol", lambda: mcp_server.inspect_symbol(Path(target_file).stem or query, target_root=target_root)),
        _sample("impact_radius", lambda: mcp_server.get_impact_radius(target_file, target_root=target_root)),
        _sample("confidence_score", lambda: mcp_server.get_confidence_score(target_file, target_root=target_root)),
        _sample("test_impact", lambda: mcp_server.get_test_impact(target_file, target_root=target_root)),
        _sample("upstream_trace", lambda: mcp_server.trace_upstream_cause(target_file, target_root=target_root)),
        _sample("patch_validation", lambda: mcp_server.validate_patch(target_file, "", target_root=target_root)),
        _sample(
            "patch_validation_missing_target",
            lambda: mcp_server.validate_patch("src/does-not-exist.ts", "export const x = 1;\n", target_root=target_root),
        ),
    ]
    if not target_root:
        samples.insert(
            3,
            _sample(
                "domain_ui_work_queue",
                lambda: mcp_server.get_violation_work_queue(rule="domain_ui_leaks", project="MAIN", page_size=3),
            ),
        )
        samples.insert(
            4,
            _sample(
                "domain_ui_all_projects_work_queue",
                lambda: mcp_server.get_violation_work_queue(rule="domain_ui_leaks", project="*", page_size=3),
            ),
        )
        samples.insert(18, _sample("confidence_missing_target", lambda: mcp_server.get_confidence_score("src/NO_SUCH_FILE.tsx")))
        samples.insert(18, _sample("test_impact_filled", lambda: mcp_server.get_test_impact("src/shared/utils/text.ts")))
        samples.insert(
            20,
            _sample(
                "upstream_trace_filled",
                lambda: mcp_server.trace_upstream_cause(upstream_trace_target),
            ),
        )
        samples.insert(
            21,
            _sample(
                "upstream_trace_path_resolution",
                lambda: mcp_server.trace_upstream_cause(target_file),
            ),
        )
        samples.insert(
            22,
            _sample(
                "patch_validation_negative",
                lambda: mcp_server.validate_patch(
                    target_file,
                    _inapplicable_exact_patch_probe(target_file),
                ),
            ),
        )
    _log("reviewing samples")
    reviews = [_review_named_sample(sample, target_root=target_root) for sample in samples]
    for review in reviews:
        review["agent_eye_lifecycle"] = _agent_eye_lifecycle(review)
    allowed_variation_samples = {
        str(value) for value in _quality_review_contract().get("allowed_variation_samples", [])
    }
    unexpected_variation_samples = [
        str(sample.get("name") or "")
        for sample in samples
        if not target_root
        and str(sample.get("name") or "") not in allowed_variation_samples
        and "Variations/" in str(sample.get("body") or "")
    ]
    target_meta = {
        "target_file": target_file,
        "search_query": query,
        "work_queue_roundtrip_target": roundtrip_target,
        "upstream_trace_target": upstream_trace_target,
        "sample_selection_policy": (
            "Derived from current target selection and work queue output; hardcoded paths are fallback-only."
        ),
    }
    selection_policy = _manual_pack_selection_policy()
    distinct_sample_targets = _sample_target_files(samples)
    analysis_root = _analysis_root_for_review(target_root)
    existing_sample_targets = [
        target for target in distinct_sample_targets if (analysis_root / target).exists()
    ]
    min_distinct_targets = int(selection_policy.get("min_distinct_quality_review_targets") or 0)
    min_existing_targets = int(selection_policy.get("min_existing_quality_review_targets") or 0)
    seal_contract = load_json_file(SEAL_CONTRACT_PATH, {})
    family_samples = {
        str(sample)
        for family in (seal_contract.get("families", []) if isinstance(seal_contract, dict) else [])
        if isinstance(family, dict)
        for sample in family.get("samples", [])
    }
    generated_sample_names = {str(sample.get("name") or "") for sample in samples}
    negative_probe_names = set(_negative_probe_samples())
    metadata_checks = {
        "target_file_selected": bool(target_file),
        "metadata_utf8_clean": _utf8_clean(target_meta),
        "roundtrip_target_is_scoped_or_file": bool(roundtrip_target)
        and ("::" in str(roundtrip_target) or "/" in str(roundtrip_target) or "\\" in str(roundtrip_target)),
        "default_agent_samples_do_not_leak_variations": not unexpected_variation_samples,
        "quality_review_has_distinct_targets": len(distinct_sample_targets) >= min_distinct_targets,
        "quality_review_has_existing_targets": len(existing_sample_targets) >= min_existing_targets,
        "family_sample_inventory_matches_generated_samples": family_samples == generated_sample_names,
        "negative_probe_purposes_are_generated_samples": bool(negative_probe_names) and negative_probe_names.issubset(generated_sample_names),
    }
    metadata_review = {
        "name": "review_metadata",
        "passed": all(metadata_checks.values()),
        "checks": metadata_checks,
        "details": {
            "unexpected_variation_samples": unexpected_variation_samples,
            "distinct_sample_targets": distinct_sample_targets,
            "existing_sample_targets": existing_sample_targets,
            "min_distinct_quality_review_targets": min_distinct_targets,
            "min_existing_quality_review_targets": min_existing_targets,
            "family_samples_missing_from_generation": sorted(family_samples - generated_sample_names),
            "generated_samples_missing_from_families": sorted(generated_sample_names - family_samples),
            "negative_probe_purposes_missing_from_generation": sorted(negative_probe_names - generated_sample_names),
        },
        "encoding_markers": [],
    }
    metadata_review["agent_eye_lifecycle"] = _agent_eye_lifecycle(metadata_review)
    reviews.append(metadata_review)
    negative_probe_samples = [
        {
            "name": str(sample.get("name") or ""),
            "purpose": _negative_probe_samples()[str(sample.get("name") or "")],
        }
        for sample in samples
        if str(sample.get("name") or "") in _negative_probe_samples()
    ]
    return {
        "meta": {
            "kind": "agent_surface_quality_review",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_agent_surface_quality_review",
            "target_root": str(Path(target_root).resolve()) if target_root else "",
        },
        "target": target_meta,
        "summary": {
            "status": "PASS" if all(row["passed"] for row in reviews) else "FAIL",
            "samples": len(samples),
            "reviews": len(reviews),
            "passed": sum(1 for row in reviews if row["passed"]),
            "negative_probe_samples": negative_probe_samples,
        },
        "manual_review_notes": [
            "Default agent-facing packets should tell a coding agent what to inspect or change next.",
            "Default directive language should be English; exact target-repository paths, symbols, and evidence snippets must remain unmodified.",
            "Internal SAGE graph/provenance identifiers belong in json or brief_debug modes, not default briefs.",
            "A missing active signal is not an all-clear; the packet must tell the agent to pick a concrete target first.",
            "Every reviewed packet still requires manual agent-eye review and final human seal before the current release is declared sealed.",
        ],
        "reviews": reviews,
        "samples": samples,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    target = payload.get("target", {})
    lines = [
        "# Agent Surface Quality Review",
        "",
        f"- status: `{summary.get('status')}`",
        f"- samples: `{summary.get('samples')}`",
        f"- reviews: `{summary.get('reviews')}`",
        f"- passed: `{summary.get('passed')}`",
        f"- negative_probe_samples: `{len(summary.get('negative_probe_samples') or [])}`",
        f"- target_file: `{target.get('target_file')}`",
        f"- search_query: `{target.get('search_query')}`",
        "",
        "## Review Checks",
        "",
        "| Sample | Result | Agent-Eye Lifecycle | Failed Checks |",
        "|---|---|---|---|",
    ]
    for row in payload.get("reviews", []):
        failed = [key for key, value in (row.get("checks") or {}).items() if not value]
        lifecycle = row.get("agent_eye_lifecycle") if isinstance(row.get("agent_eye_lifecycle"), dict) else {}
        lifecycle_text = (
            f"{lifecycle.get('automated_agent_eye_check', 'unknown')}; "
            f"manual_agent_eye={lifecycle.get('manual_agent_eye_review', 'required')}; "
            f"human_seal={lifecycle.get('human_seal_status', 'not_human_sealed')}"
        )
        lines.append(
            f"| `{row.get('name')}` | {'PASS' if row.get('passed') else 'FAIL'} | "
            f"`{lifecycle_text}` | `{', '.join(failed) if failed else '-'}` |"
        )
    lines.extend(["", "## Manual Review Notes", ""])
    for note in payload.get("manual_review_notes", []):
        lines.append(f"- {note}")
    negative_probes = summary.get("negative_probe_samples")
    if isinstance(negative_probes, list) and negative_probes:
        lines.extend(["", "## Intentional Negative Probes", ""])
        for item in negative_probes:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('name')}`: {item.get('purpose')}")
    lines.extend(["", "## Sample Excerpts", ""])
    for sample in payload.get("samples", []):
        lines.extend(
            [
                f"### {sample.get('name')}",
                "",
                "```text",
                str(sample.get("excerpt") or "").replace("```", "'''"),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate agent-facing surface quality review.")
    parser.add_argument("--target-root", default="", help="Optional external target repository root.")
    args = parser.parse_args()
    payload = build_review(target_root=args.target_root)
    raw_dir = _raw_dir_for_review(args.target_root)
    reports_dir = _reports_dir_for_review(args.target_root)
    save_json_atomic(raw_dir / "agent_surface_quality_review.json", payload)
    save_text_atomic(reports_dir / "agent_surface_quality_review.md", render_report(payload))
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
