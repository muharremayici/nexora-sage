from __future__ import annotations

import json
import posixpath
from pathlib import Path
from typing import Any, Callable

from tools.core.capability_activation import load_capability_activation_plan, relevant_activation_context
from tools.core.agent_command_contracts import command_contracts_for_agent, target_repo_validation_policy, target_repo_validation_tools
from tools.core.capability_registry import load_capability_registry, relevant_capability_contracts
from tools.core.reality_scope import TARGET_REPOSITORY_PROJECTION_ID, projection_capability_scopes
from tools.core.architecture_blueprints import architecture_governance_context
from tools.core.atlas_io import load_atlas_data
from tools.core.config import RAW_DIR
from tools.core.json_io import load_json_file
from tools.core.audit_rules import build_rule_taxonomy
from tools.core.principle_packs import principles_for_context
from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
from tools.core.contextos_signal_limits import contextos_signal_limit
from tools.core.path_identity import strip_current_directory_prefix
from tools.core.import_classifier import import_specifier_from_audit_detail


MASKED_BODY = "// [MASKED] This file is restricted/locked under SAGE architectural governance rules."
_AGENT_ATLAS_CACHE: dict[str, Any] | None = None
_APPROVAL_REQUIRED_RULE_MODES = frozenset({"enforced", "heal", "critical"})
_ACTIONABILITY_VALUES = frozenset({"actionable_proposal", "orientation_only", "no_action"})


def target_directive_actionability_projection(
    actionability: str,
    *,
    evidence_source: str,
) -> dict[str, Any]:
    """Keep attention, mutation proposals and approval semantics separate."""
    normalized = str(actionability or "").strip().lower()
    if normalized not in _ACTIONABILITY_VALUES:
        raise ValueError(f"Unsupported target directive actionability: {actionability!r}")
    mutation_proposed = normalized == "actionable_proposal"
    return {
        "actionability": normalized,
        "mutation_proposed": mutation_proposed,
        "mutation_authority": (
            "separate_actor_policy_and_approval"
            if mutation_proposed
            else "not_granted_by_this_directive"
        ),
        "actionability_evidence_source": str(evidence_source or "not_declared"),
        "approval_is_not_mutation_authority": True,
        "approval_requirement_scope": (
            "proposed_mutation"
            if mutation_proposed
            else "not_applicable_without_separate_actionable_mutation"
        ),
    }


def target_directive_approval_projection(
    rule_explanation: dict[str, Any] | None,
    *,
    approval_required: bool | None = None,
    approval_basis: str = "",
) -> dict[str, Any]:
    """Project one canonical, bounded authority explanation for target-repo directives."""
    explanation = rule_explanation if isinstance(rule_explanation, dict) else {}
    rule_mode = str(explanation.get("mode") or "unknown").strip().lower() or "unknown"
    mode_requires_approval = rule_mode in _APPROVAL_REQUIRED_RULE_MODES
    decision_source = "rule_mode" if approval_required is None else "explicit_override"
    required = mode_requires_approval if approval_required is None else bool(approval_required)
    rationale = " ".join(str(explanation.get("rationale") or "").split())
    basis = " ".join(str(approval_basis or "").split())
    if required and mode_requires_approval:
        decision = f"Rule mode '{rule_mode}' requires explicit human approval before mutation."
    elif required:
        decision = f"This directive requires explicit human approval despite rule mode '{rule_mode}'."
    elif mode_requires_approval:
        decision = f"This directive does not require a separate human approval despite rule mode '{rule_mode}'."
    else:
        decision = f"Rule mode '{rule_mode}' does not require a separate human approval for this directive."
    reason = " ".join(part for part in (decision, basis, rationale) if part)
    max_chars = max(1, COMPACT_AGENT_PACKET_TOKENS // 4)
    if len(reason) > max_chars:
        shortened = reason[: max_chars - 1].rsplit(" ", 1)[0].rstrip(".,;:")
        reason = f"{shortened}." if shortened else decision
    return {
        "rule_mode": rule_mode,
        "human_approval_required": required,
        "approval_decision_source": decision_source,
        "approval_reason": reason,
    }


def is_restricted_or_locked(path_str: str, content: str) -> bool:
    path_lower = str(path_str or "").lower()
    if ".env" in path_lower or "credentials" in path_lower or "secret" in path_lower:
        return True
    if "architecture_doctrine.json" in path_lower or ("pipeline" in path_lower and path_lower.endswith(".lock")):
        return True
    content_lower = str(content or "").lower()
    for token in ["api_key", "apikey", "private_key", "password_hash", "client_secret", "access_token", "refresh_token", "bearer "]:
        if token in content_lower:
            return True
    return False


def safe_signal_body(
    rel_path: str,
    project_key: str,
    max_chars: int,
    resolve_absolute_path: Callable[[str, str], Path],
) -> dict[str, Any]:
    payload = {
        "path": rel_path,
        "language": "txt",
        "masked": False,
        "truncated": False,
        "body": "",
    }
    # Path traversal validation
    cleaned_rel = rel_path.replace("\\", "/").lstrip("/")
    parts = cleaned_rel.split("/")
    if ".." in parts or any(p == "" for p in parts):
        payload["body"] = "[Path traversal attempt blocked]"
        return payload

    abs_path = resolve_absolute_path(rel_path, project_key)
    payload["language"] = abs_path.suffix.lstrip(".") if abs_path.suffix else "txt"
    if not abs_path.exists() or not abs_path.is_file():
        payload["body"] = "[File not found on disk]"
        return payload
    try:
        raw_body = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        payload["body"] = f"[Error reading file: {exc}]"
        return payload
    if is_restricted_or_locked(rel_path, raw_body):
        payload["masked"] = True
        payload["body"] = MASKED_BODY
        return payload
    if max_chars > 0 and len(raw_body) > max_chars:
        payload["truncated"] = True
        bounded_body = raw_body[:max_chars]
        last_newline = bounded_body.rfind("\n")
        if last_newline > 0:
            bounded_body = bounded_body[:last_newline].rstrip()
        payload["body"] = bounded_body + "\n// [TRUNCATED] ContextOS max_chars_per_file limit reached at a line boundary."
        return payload
    payload["body"] = raw_body
    return payload


def sanitize_signal(sig: dict[str, Any]) -> dict[str, Any]:
    node_key = str(sig.get("node_key") or "").strip()
    relative_path = str(sig.get("relative_path") or "").strip().replace("\\", "/")
    project_key = node_key.split("::", 1)[0] if "::" in node_key else "MAIN"
    target_ref = str(sig.get("target_ref") or "").strip()
    if not target_ref and relative_path:
        target_ref = f"{project_key}::{relative_path}"
    direct = list(sig.get("direct_dependents", []) or [])
    transitive = list(sig.get("transitive_dependents", []) or [])
    active_violations = list(sig.get("active_violations", []) or [])
    circular_cycles = list(sig.get("circular_cycles", []) or [])
    focus_halo = list(sig.get("focus_halo", []) or [])
    direct_count = int(sig.get("direct_dependents_count") or len(direct))
    transitive_count = int(sig.get("transitive_dependents_count") or len(transitive))
    direct = direct[: contextos_signal_limit("direct_dependents")]
    transitive = transitive[: contextos_signal_limit("transitive_dependents")]
    active_violations = active_violations[: contextos_signal_limit("active_violations")]
    circular_cycles = circular_cycles[: contextos_signal_limit("circular_cycles")]
    focus_halo = focus_halo[: contextos_signal_limit("agent_related_files")]
    return {
        "node_key": node_key,
        "relative_path": relative_path,
        "target_ref": target_ref,
        "signal_kind": sig.get("signal_kind", "source"),
        "impact_score": sig.get("impact_score", 0.0),
        "impact_score_status": sig.get("impact_score_status", "not_declared"),
        "direct_dependents_count": direct_count,
        "transitive_dependents_count": transitive_count,
        "direct_dependents": direct,
        "direct_dependents_omitted": max(
            int(sig.get("direct_dependents_omitted") or 0),
            direct_count - len(direct),
        ),
        "transitive_dependents": transitive,
        "transitive_dependents_omitted": max(
            int(sig.get("transitive_dependents_omitted") or 0),
            transitive_count - len(transitive),
        ),
        "focus_halo": focus_halo,
        "reasoning_breadcrumbs": list(sig.get("reasoning_breadcrumbs", []) or [])[:8],
        "signal_actionability": sig.get("signal_actionability", {}),
        "active_violations": active_violations,
        "active_violations_omitted": max(
            int(sig.get("active_violations_omitted") or 0),
            len(list(sig.get("active_violations", []) or [])) - len(active_violations),
        ),
        "circular_cycles": circular_cycles,
        "circular_cycles_omitted": max(
            int(sig.get("circular_cycles_omitted") or 0),
            len(list(sig.get("circular_cycles", []) or [])) - len(circular_cycles),
        ),
        "circular_cycles_status": sig.get("circular_cycles_status", "not_declared"),
        "risk_claim_boundary": sig.get("risk_claim_boundary", "not_declared"),
    }


def _split_atlas_node(path_text: str, default_project: str | None = None) -> tuple[str | None, str]:
    text = str(path_text or "").strip().replace("\\", "/")
    if "::" in text:
        project_key, rel_path = text.split("::", 1)
        return project_key or default_project, rel_path.strip().lstrip("/")
    return default_project, text.strip().lstrip("/")


def _atlas_file_meta(project_key: str | None, rel_path: str) -> dict[str, Any]:
    global _AGENT_ATLAS_CACHE
    if not project_key or not rel_path:
        return {}
    if _AGENT_ATLAS_CACHE is None:
        _AGENT_ATLAS_CACHE = load_atlas_data()
    atlas = _AGENT_ATLAS_CACHE
    project = atlas.get(project_key, {}) if isinstance(atlas, dict) else {}
    files = project.get("files", {}) if isinstance(project, dict) else {}
    if not isinstance(files, dict):
        return {}
    direct = files.get(rel_path)
    if isinstance(direct, dict):
        return {**direct, "_atlas_rel": rel_path}
    candidates = [
        f"{rel_path}.ts",
        f"{rel_path}.tsx",
        f"{rel_path}.js",
        f"{rel_path}.jsx",
        f"{rel_path}/index.ts",
        f"{rel_path}/index.tsx",
        f"{rel_path}/index.js",
        f"{rel_path}/index.jsx",
    ]
    for candidate in candidates:
        direct = files.get(candidate)
        if isinstance(direct, dict):
            return {**direct, "_atlas_rel": candidate}
    # Some Atlas file keys are source-root relative while the edit surface needs
    # workspace-relative paths. Accept either shape so agent packets can show the
    # path an editor should actually open.
    for _key, value in files.items():
        if isinstance(value, dict) and str(value.get("workspace_rel") or "").replace("\\", "/") == rel_path:
            return {**value, "_atlas_rel": str(_key).replace("\\", "/")}
    return {}


def _agent_file_context(path_text: str, *, default_project: str | None = None) -> dict[str, Any]:
    project_key, rel_path = _split_atlas_node(path_text, default_project=default_project)
    meta = _atlas_file_meta(project_key, rel_path) if project_key else {}
    workspace_rel = str(meta.get("workspace_rel") or "").replace("\\", "/").strip()
    atlas_rel = str(meta.get("_atlas_rel") or rel_path).replace("\\", "/").strip()
    repo_relative = workspace_rel or atlas_rel
    context = {
        "repo_relative_path": repo_relative,
        "atlas_relative_path": atlas_rel,
        "project_key": project_key,
        "atlas_node": f"{project_key}::{atlas_rel}" if project_key and atlas_rel else "",
    }
    return context


def _agent_file_fields(paths: list[str], *, default_project: str | None = None) -> dict[str, Any]:
    contexts: list[dict[str, Any]] = []
    seen_repo: set[str] = set()
    for path in paths:
        context = _agent_file_context(path, default_project=default_project)
        repo_path = context.get("repo_relative_path")
        if not repo_path or repo_path in seen_repo:
            continue
        seen_repo.add(repo_path)
        contexts.append(context)
    return {
        "target_files": [row["repo_relative_path"] for row in contexts],
        "atlas_nodes": [row["atlas_node"] for row in contexts if row.get("atlas_node")],
        "file_context": contexts,
    }


def _signal_file_fields(signal: dict[str, Any]) -> dict[str, Any]:
    node_key = str(signal.get("node_key") or "").strip()
    relative = str(signal.get("relative_path") or "").strip()
    project_key, _rel = _split_atlas_node(node_key)
    target_context = _agent_file_context(node_key or relative, default_project=project_key)
    target_file = target_context.get("repo_relative_path")
    direct = [
        _agent_file_context(str(item), default_project=project_key)
        for item in signal.get("direct_dependents", []) or []
        if item
    ]
    halo = [
        _agent_file_context(str(item.get("node_key") or item.get("relative_path") or ""), default_project=project_key)
        for item in signal.get("focus_halo", []) or []
        if isinstance(item, dict) and (item.get("node_key") or item.get("relative_path"))
    ]
    related_contexts = []
    seen = set()
    for context in direct + halo:
        repo_path = context.get("repo_relative_path")
        if repo_path and repo_path not in seen:
            seen.add(repo_path)
            related_contexts.append(context)
    related_limit = contextos_signal_limit("agent_related_files")
    return {
        "target_files": [target_file] if target_file else [],
        "atlas_nodes": [target_context["atlas_node"]] if target_context.get("atlas_node") else [],
        "file_context": [target_context] if target_file else [],
        "related_files": [row["repo_relative_path"] for row in related_contexts][:related_limit],
    }


def _rule_explanation(rule: str, audit_report: dict[str, Any] | None = None) -> dict[str, Any]:
    taxonomy = audit_report.get("rule_taxonomy", {}) if isinstance(audit_report, dict) else {}
    profiles = taxonomy.get("profiles", {}) if isinstance(taxonomy, dict) else {}
    profile = profiles.get(rule, {}) if isinstance(profiles, dict) else {}
    if not profile:
        profile = (build_rule_taxonomy().get("profiles", {}) or {}).get(rule, {})
    if isinstance(profile, dict) and profile:
        return {
            "rule": rule,
            "label": profile.get("label") or rule,
            "mode": profile.get("mode"),
            "layer": profile.get("layer"),
            "rationale": profile.get("rationale") or "Architecture doctrine rule matched this file.",
        }
    return {
        "rule": rule,
        "label": rule.replace("_", " ").title(),
        "mode": None,
        "layer": None,
        "rationale": "SAGE reported this rule; inspect the cited file and source artifact before changing code.",
    }


def _import_evidence_related_files(
    file_path: str,
    detail: str,
    *,
    default_project: str | None = None,
) -> list[str]:
    """Return editor-openable related files from simple import evidence."""
    imported = import_specifier_from_audit_detail(detail)
    if not imported.startswith("."):
        return []
    _project_key, rel_path = _split_atlas_node(file_path, default_project=default_project)
    base_dir = posixpath.dirname(rel_path.replace("\\", "/"))
    candidate = strip_current_directory_prefix(
        posixpath.normpath(posixpath.join(base_dir, imported.replace("\\", "/")))
    )
    if candidate.startswith("../") or not candidate:
        return []
    if not _atlas_file_meta(_project_key, candidate):
        return []
    context = _agent_file_context(candidate, default_project=_project_key)
    repo_path = str(context.get("repo_relative_path") or "").strip()
    return [repo_path] if repo_path else []


def _directive_for_violation(
    violation: dict[str, Any],
    audit_report: dict[str, Any] | None,
    *,
    index: int,
) -> dict[str, Any]:
    file_path = str(violation.get("file") or violation.get("path") or "").strip()
    project_key = str(violation.get("project") or violation.get("project_key") or "").strip() or None
    rule = str(violation.get("rule") or "architecture_violation").strip()
    detail = str(violation.get("detail") or violation.get("message") or "").strip()
    explanation = _rule_explanation(rule, audit_report)
    file_fields = _agent_file_fields([file_path] if file_path else [], default_project=project_key)
    related_files = _import_evidence_related_files(file_path, detail, default_project=project_key)
    finding_semantics = {
        field: violation.get(field)
        for field in (
            "symbol_name",
            "symbol_kind",
            "threshold_kind",
            "start_line",
            "end_line",
            "observed_loc",
            "limit",
        )
        if field in violation
    }
    if finding_semantics and rule.startswith("loc_limits_"):
        start_line = finding_semantics.get("start_line")
        end_line = finding_semantics.get("end_line")
        span_label = (
            f"L{start_line}-L{end_line}"
            if isinstance(start_line, int) and isinstance(end_line, int)
            else "the reported symbol span"
        )
        action = (
            f"Inspect only {finding_semantics.get('symbol_name') or 'the reported symbol'} at "
            f"{span_label} first. "
            f"Treat this as a {finding_semantics.get('symbol_kind') or 'symbol'} maintainability review "
            f"({finding_semantics.get('observed_loc')} LOC; {finding_semantics.get('threshold_kind')} limit "
            f"{finding_semantics.get('limit')}), not proof that decomposition is automatically correct. "
            "Refactor only when bounded source evidence shows separable responsibilities, and preserve the public contract."
        )
    else:
        action = (
            "Open the target file, identify the import/dependency or implementation pattern described by "
            "the evidence, then change the smallest code region that satisfies the rule rationale. Do not "
            "silence the rule unless the source artifact proves it is a false positive."
        )
    directive = {
        "id": f"audit_violation_{index}",
        "intent": "fix_architecture_violation",
        **file_fields,
        "related_files": related_files,
        "rule": rule,
        "rule_explanation": explanation,
        "evidence": detail,
        "action": action,
        "validation_tools": target_repo_validation_tools(),
        "source_artifacts": ["output/.raw/audit_report.json", "config/architecture_doctrine.json"],
        **target_directive_actionability_projection(
            "actionable_proposal",
            evidence_source="audit_violation",
        ),
        **target_directive_approval_projection(explanation),
        "confidence": "high" if detail else "medium",
    }
    if finding_semantics:
        directive["finding_semantics"] = finding_semantics
    return directive


def _matches_project_scope(value: str, project_scope: str) -> bool:
    scope = str(project_scope or "").strip()
    if not scope or scope in {"*", "all", "ALL"}:
        return True
    text = str(value or "").strip()
    if not text:
        return False
    if "::" in text:
        return text.split("::", 1)[0].upper() == scope.upper()
    return text.upper() == scope.upper()


def _violation_matches_project_scope(violation: dict[str, Any], project_scope: str) -> bool:
    if not project_scope or project_scope in {"*", "all", "ALL"}:
        return True
    explicit_project = str(violation.get("project") or violation.get("project_key") or "").strip()
    if explicit_project:
        return _matches_project_scope(explicit_project, project_scope)

    file_path = str(violation.get("file") or violation.get("path") or "").strip().replace("\\", "/")
    if "::" in file_path:
        return _matches_project_scope(file_path, project_scope)

    # Legacy audit rows often only carry a repo-relative path. Treat plain
    # repo-relative rows as MAIN, but do not silently pull variation paths into
    # the default target-repo edit scope.
    if project_scope.upper() == "MAIN" and file_path and not file_path.startswith("Variations/"):
        return True
    return False


def _signal_matches_project_scope(signal: dict[str, Any], project_scope: str) -> bool:
    if not project_scope or project_scope in {"*", "all", "ALL"}:
        return True
    return _matches_project_scope(
        str(signal.get("node_key") or signal.get("target_ref") or ""),
        project_scope,
    )


def _directive_for_signal(signal: dict[str, Any], *, index: int) -> dict[str, Any]:
    file_fields = _signal_file_fields(signal)
    breadcrumbs = [str(item) for item in signal.get("reasoning_breadcrumbs", []) or [] if item]
    broad_proof_deferred = (
        signal.get("impact_score_status") in {"deferred_broad_proof", "scoped_dependency_lower_bound"}
        or signal.get("circular_cycles_status") in {"deferred_broad_proof", "scoped_scc_membership"}
    )
    evidence_boundary = {
        "risk_claim_boundary": signal.get("risk_claim_boundary", "not_declared"),
        "impact_score_status": signal.get("impact_score_status", "not_declared"),
        "circular_cycles_status": signal.get("circular_cycles_status", "not_declared"),
        "direct_dependents_omitted": signal.get("direct_dependents_omitted", 0),
        "transitive_dependents_omitted": signal.get("transitive_dependents_omitted", 0),
        "active_violations_omitted": signal.get("active_violations_omitted", 0),
    }
    explanation = {
        "rule": "contextos_active_focus",
        "label": "ContextOS Active Focus",
        "mode": "advisory",
        "layer": "context",
        "rationale": "This file is hot because it changed or sits inside the current dependency halo.",
    }
    return {
        "id": f"contextos_focus_{index}",
        "intent": (
            "inspect_hot_file_and_dependency_halo"
            if broad_proof_deferred
            else "inspect_hot_file_and_blast_radius"
        ),
        **file_fields,
        "rule": "contextos_active_focus",
        "rule_explanation": explanation,
        "evidence": "; ".join(breadcrumbs[:3]),
        "action": (
            "Inspect the target and fresh Atlas dependency halo before editing. Broad cycle membership and "
            "blast-score proof is deferred; do not interpret an empty cycle list or missing score as absence."
            if broad_proof_deferred
            else (
                "Inspect the target file first, then inspect listed related files before editing. If a failing "
                "symptom appears in a related file, trace upstream through the listed focus file before patching."
            )
        ),
        "evidence_boundary": evidence_boundary,
        "validation_tools": target_repo_validation_tools(),
        "source_artifacts": (
            ["output/.raw/signals.json", "output/.raw/watchdog_audit_report.json", "output/.raw/atlas.json"]
            if broad_proof_deferred
            else ["output/.raw/signals.json", "output/.raw/blast_radius.json"]
        ),
        **target_directive_actionability_projection(
            "orientation_only",
            evidence_source="contextos_hotness_or_dependency_halo",
        ),
        **target_directive_approval_projection(explanation),
        "confidence": "medium",
    }


def _extract_action_paths(value: Any, *, limit: int = 12) -> list[str]:
    paths: list[str] = []
    path_keys = {"file", "path", "target_path", "target_file", "relative_path", "source_path"}

    def add(candidate: Any) -> None:
        if len(paths) >= limit:
            return
        text = str(candidate or "").strip().replace("\\", "/")
        if not text or text in paths:
            return
        if "::" in text or any(text.endswith(ext) for ext in (".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".java", ".cs", ".json", ".md", ".css", ".scss")):
            paths.append(text)

    def walk(item: Any) -> None:
        if len(paths) >= limit:
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key) in path_keys:
                    add(child)
                else:
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            add(item)

    walk(value)
    return paths[:limit]


def _directive_for_quality_check(check: dict[str, Any], *, index: int) -> dict[str, Any]:
    name = str(check.get("name") or "quality_attention").strip()
    details = check.get("details", [])
    paths = _extract_action_paths(details)
    file_fields = _agent_file_fields(paths)
    source_artifacts = ["output/.raw/quality_gate.json"]
    explanation = {
        "rule": name,
        "label": name.replace("_", " ").title(),
        "mode": "advisory",
        "layer": "quality",
        "rationale": "Quality gate reported attention. Use details to choose a narrower engine report before editing.",
    }
    return {
        "id": f"quality_attention_{index}",
        "intent": "review_quality_attention",
        **file_fields,
        "related_files": [],
        "rule": name,
        "rule_explanation": explanation,
        "evidence": json.dumps(details[:1], ensure_ascii=False)[:900] if isinstance(details, list) else str(details)[:900],
        "action": (
            "Do not patch broadly from this summary alone. Open the cited source artifact, extract concrete file "
            "paths, then run the relevant focused validator before proposing code changes."
        ),
        "validation_tools": target_repo_validation_tools(),
        "source_artifacts": source_artifacts,
        **target_directive_actionability_projection(
            "orientation_only",
            evidence_source="aggregate_quality_attention",
        ),
        **target_directive_approval_projection(explanation),
        "confidence": "low",
    }


def _directive_for_priority_finding(finding: dict[str, Any], *, index: int) -> dict[str, Any]:
    file_path = str(finding.get("scoped_file") or finding.get("file") or "").strip()
    related_files = [str(item) for item in finding.get("related_files", []) or [] if item]
    file_fields = _agent_file_fields([file_path] if file_path else [])
    related_fields = _agent_file_fields(related_files)
    classification = str(finding.get("classification") or "priority_repository_finding").strip()
    risk_tier = str(finding.get("risk_tier") or "").strip()
    why_problematic = str(finding.get("why_problematic") or "").strip()
    recommended_action = str(finding.get("recommended_action") or "").strip()
    explanation = {
        "rule": classification,
        "label": classification.replace("_", " ").title(),
        "mode": "advisory",
        "layer": "repository_surface",
        "rationale": why_problematic or "A prioritized repository finding points at this file and should be reviewed before editing broadly.",
    }
    approval_required = risk_tier.lower() in {"critical", "high"}
    approval_basis = (
        f"The target finding risk tier is '{risk_tier.lower()}'."
        if risk_tier
        else "The target finding does not declare an elevated risk tier."
    )
    return {
        "id": f"priority_finding_{index}",
        "intent": "review_priority_repository_finding",
        **file_fields,
        "related_files": related_fields.get("target_files", []),
        "rule": classification,
        "rule_explanation": explanation,
        "evidence": why_problematic,
        "action": recommended_action or "Inspect the target and related files, then decide whether a minimal patch is warranted.",
        "source_artifacts": ["output/.raw/live_surface_priority_pack.json"],
        **target_directive_actionability_projection(
            "orientation_only",
            evidence_source="prioritized_repository_attention",
        ),
        **target_directive_approval_projection(
            explanation,
            approval_required=approval_required,
            approval_basis=approval_basis,
        ),
        "confidence": str(finding.get("confidence") or "medium").lower(),
    }


def build_agent_action_directives(
    signals_data: dict[str, Any] | None = None,
    *,
    audit_report: dict[str, Any] | None = None,
    quality_gate: dict[str, Any] | None = None,
    priority_pack: dict[str, Any] | None = None,
    max_items: int = 8,
    project_scope: str = "",
) -> list[dict[str, Any]]:
    """Return bounded, patch-oriented instructions for AI agents.

    These directives intentionally translate internal SAGE artifacts into
    action fields an agent can use: concrete files, rule explanations,
    validation commands, source artifacts and approval posture.
    """
    max_items = max(1, min(int(max_items or 8), 20))
    directives: list[dict[str, Any]] = []

    violations = audit_report.get("violations", []) if isinstance(audit_report, dict) else []
    for violation in violations:
        if isinstance(violation, dict) and _violation_matches_project_scope(violation, project_scope):
            directives.append(_directive_for_violation(violation, audit_report, index=len(directives) + 1))
        if len(directives) >= max_items:
            return directives

    active_signals = signals_data.get("active_signals", []) if isinstance(signals_data, dict) else []
    for signal in active_signals:
        if isinstance(signal, dict) and _signal_matches_project_scope(signal, project_scope):
            directives.append(_directive_for_signal(sanitize_signal(signal), index=len(directives) + 1))
        if len(directives) >= max_items:
            return directives

    priority_items = priority_pack.get("items", []) if isinstance(priority_pack, dict) else []
    for item in priority_items:
        if isinstance(item, dict) and (item.get("scoped_file") or item.get("file")):
            directives.append(_directive_for_priority_finding(item, index=len(directives) + 1))
        if len(directives) >= max_items:
            return directives

    checks = quality_gate.get("checks", []) if isinstance(quality_gate, dict) else []
    for check in checks:
        details = check.get("details", []) if isinstance(check, dict) else []
        if (
            isinstance(check, dict)
            and check.get("passed") is False
            and "expected_failure_drill" not in json.dumps(details, ensure_ascii=False)
            and _extract_action_paths(details)
        ):
            directives.append(_directive_for_quality_check(check, index=len(directives) + 1))
        if len(directives) >= max_items:
            return directives

    if not directives:
        explanation = {
            "rule": "no_active_contextos_or_audit_action",
            "label": "No Active Patch Directive",
            "mode": "informational",
            "layer": "agent",
            "rationale": "No active ContextOS signal or audit violation is currently asking for a targeted patch.",
        }
        directives.append(
            {
                "id": "no_active_patch_directive",
                "intent": "no_immediate_code_patch",
                "target_files": [],
                "related_files": [],
                "rule": "no_active_contextos_or_audit_action",
                "rule_explanation": explanation,
                "evidence": "Use explicit user intent, inspect_file/inspect_symbol, or a focused validator before editing.",
                "action": "Do not invent cleanup work from broad summaries. Ask for or derive a concrete target before changing code.",
                "validation_tools": target_repo_validation_tools(),
                "source_artifacts": [
                    "output/.raw/signals.json",
                    "output/.raw/audit_report.json",
                    "output/.raw/quality_gate.json",
                ],
                **target_directive_actionability_projection(
                    "no_action",
                    evidence_source="no_actionable_directive",
                ),
                **target_directive_approval_projection(explanation),
                "confidence": "high",
            }
        )
    return directives[:max_items]


def build_upstream_trace(target_node: str, circular_deps_data: dict[str, Any], signals_data: dict[str, Any] | None = None) -> dict[str, Any]:
    target = str(target_node or "").strip()
    edges = circular_deps_data.get("edges", []) if isinstance(circular_deps_data, dict) else []
    upstream_dependencies: list[str] = []
    direct_dependents: list[str] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target_dep = str(edge.get("target") or "")
        if source == target and target_dep:
            upstream_dependencies.append(target_dep)
        if target_dep == target and source:
            direct_dependents.append(source)

    candidate_changed_sources: list[dict[str, Any]] = []
    if isinstance(signals_data, dict):
        for signal in signals_data.get("active_signals", []) or []:
            if not isinstance(signal, dict):
                continue
            signal_node = str(signal.get("node_key") or "")
            direct = {str(item) for item in signal.get("direct_dependents", []) or []}
            transitive = {str(item) for item in signal.get("transitive_dependents", []) or []}
            if target == signal_node or target in direct or target in transitive:
                relation = "self" if target == signal_node else ("direct_dependent" if target in direct else "transitive_dependent")
                candidate_changed_sources.append(
                    {
                        "source_node": signal_node,
                        "source_path": signal.get("relative_path", ""),
                        "relation": relation,
                        "reason": f"`{target}` is in the {relation} radius of active signal `{signal_node}`.",
                    }
                )

    upstream_all = sorted(set(upstream_dependencies))
    dependent_all = sorted(set(direct_dependents))
    related_limit = contextos_signal_limit("agent_related_files")
    upstream_nodes = upstream_all[:related_limit]
    dependent_nodes = dependent_all[:related_limit]
    target_context = _agent_file_context(target)
    upstream_contexts = [_agent_file_context(item) for item in upstream_nodes]
    dependent_contexts = [_agent_file_context(item) for item in dependent_nodes]
    return {
        "target": target,
        "target_file": target_context.get("repo_relative_path"),
        "target_file_context": target_context,
        "candidate_changed_sources": candidate_changed_sources[:related_limit],
        "candidate_changed_sources_count": len(candidate_changed_sources),
        "candidate_changed_sources_omitted": max(0, len(candidate_changed_sources) - related_limit),
        "upstream_dependencies": upstream_nodes,
        "upstream_dependencies_count": len(upstream_all),
        "upstream_dependencies_omitted": max(0, len(upstream_all) - len(upstream_nodes)),
        "upstream_dependency_files": [row["repo_relative_path"] for row in upstream_contexts if row.get("repo_relative_path")],
        "upstream_dependency_context": upstream_contexts,
        "direct_dependents": dependent_nodes,
        "direct_dependents_count": len(dependent_all),
        "direct_dependents_omitted": max(0, len(dependent_all) - len(dependent_nodes)),
        "direct_dependent_files": [row["repo_relative_path"] for row in dependent_contexts if row.get("repo_relative_path")],
        "direct_dependent_context": dependent_contexts,
        "reasoning": [
            "Upstream dependencies are files the target imports or depends on.",
            "Candidate changed sources are active ContextOS files whose radius contains the target.",
            "Use *_files fields for editor navigation and *_context / atlas nodes for SAGE graph identity.",
        ],
    }


def _compact_signal_for_agent_packet(signal: dict[str, Any]) -> dict[str, Any]:
    """Bound repeated graph samples while retaining canonical totals."""
    compact = dict(signal)
    sample_limit = contextos_signal_limit("agent_related_files")
    for field in ("direct_dependents", "transitive_dependents"):
        values = list(compact.get(field, []) or [])
        total_field = f"{field}_count"
        omitted_field = f"{field}_omitted"
        total = max(int(compact.get(total_field) or len(values)), len(values))
        shown = values[:sample_limit]
        compact[field] = shown
        compact[total_field] = total
        compact[omitted_field] = max(int(compact.get(omitted_field) or 0), total - len(shown))
    compact["focus_halo"] = list(compact.get("focus_halo", []) or [])[:sample_limit]
    return compact


def build_surgical_operation_packet(
    signals_data: dict[str, Any],
    *,
    circular_deps_data: dict[str, Any] | None = None,
    audit_report: dict[str, Any] | None = None,
    quality_gate: dict[str, Any] | None = None,
    priority_pack: dict[str, Any] | None = None,
    max_signals: int | None = None,
    project_scope: str = "MAIN",
    raw_dir: Path | None = None,
) -> dict[str, Any]:
    packet_raw_dir = Path(raw_dir or RAW_DIR)
    active_signals = [
        sanitize_signal(sig)
        for sig in signals_data.get("active_signals", [])
        if isinstance(sig, dict) and _signal_matches_project_scope(sig, project_scope)
    ]
    max_signals = max(
        1,
        min(
            int(max_signals or contextos_signal_limit("agent_directives")),
            contextos_signal_limit("agent_directives"),
        ),
    )
    visible = [_compact_signal_for_agent_packet(signal) for signal in active_signals[:max_signals]]
    active_projects = {
        str(signal.get("node_key", "")).split("::", 1)[0]
        for signal in visible
        if "::" in str(signal.get("node_key", ""))
    }
    halo = []
    for signal in visible:
        for item in signal.get("focus_halo", []) or []:
            if isinstance(item, dict):
                halo.append(item)
    seen_halo = set()
    deduped_halo = []
    for item in halo:
        key = item.get("node_key")
        if key and key not in seen_halo:
            seen_halo.add(key)
            deduped_halo.append(item)

    upstream_traces = []
    if circular_deps_data:
        for signal in visible:
            upstream_traces.append(build_upstream_trace(signal.get("node_key", ""), circular_deps_data, signals_data))
    registry = load_capability_registry()
    activation_plan = load_capability_activation_plan()
    execution_contract = load_json_file(packet_raw_dir / "pipeline_execution_contract_validation.json", {})
    execution_summary = execution_contract.get("summary", {}) if isinstance(execution_contract, dict) else {}
    execution_modes = execution_contract.get("execution_modes", {}) if isinstance(execution_contract, dict) else {}
    engine_signal_contract = load_json_file(packet_raw_dir / "engine_signal_contract_validation.json", {})
    architecture_oracle = load_json_file(packet_raw_dir / "architecture_oracle.json", {})
    approval_ledger = load_json_file(packet_raw_dir / "hitl_approval_ledger.json", {})
    signal_summary = engine_signal_contract.get("summary", {}) if isinstance(engine_signal_contract, dict) else {}
    signal_contracts = [
        row
        for row in engine_signal_contract.get("contract_summary", [])
        if isinstance(row, dict) and row.get("capability_id") in {"contextos", "agent_surface", "dependency_health", "test_impact"}
    ]

    return {
        "meta": {
            "kind": "contextos_surgical_operation_packet",
            "version": "v1",
            "generator": "tools.core.contextos_mcp.build_surgical_operation_packet",
        },
        "summary": {
            **(signals_data.get("summary", {}) if isinstance(signals_data.get("summary"), dict) else {}),
            "returned_focus_files": len(visible),
            "returned_halo_files": len(deduped_halo),
        },
        "mission_brief": [
            "Start with L1 focus files, then inspect first-ring halo files before broad refactors.",
            "Treat scene pivot as a request to refresh context before making architectural decisions.",
            "Use upstream traces when a failing file is downstream of a recent active signal.",
        ],
        "architecture_governance_context": architecture_governance_context(
            architecture_oracle,
            approval_ledger,
            max_projects=3,
            project_ids=active_projects or None,
        ),
        "relevant_capabilities": relevant_capability_contracts(
            registry,
            capability_ids=["contextos", "dependency_health", "test_impact", "agent_surface"],
            artifacts=["signals", "circular_deps", "blast_radius", "test_impact_report"],
            limit=6,
            allowed_system_scopes=projection_capability_scopes(TARGET_REPOSITORY_PROJECTION_ID),
        ),
        "capability_activation": relevant_activation_context(
            activation_plan,
            capability_ids=["contextos", "dependency_health", "test_impact", "agent_surface", "capability_activation_planner"],
            limit=6,
            max_projects=3,
            include_disabled=False,
            allowed_system_scopes=projection_capability_scopes(TARGET_REPOSITORY_PROJECTION_ID),
            project_ids=[project_scope] if project_scope else None,
        ),
        "pipeline_execution_contract": {
            "summary": {
                "status": execution_summary.get("status"),
                "steps": execution_summary.get("steps"),
                "scheduler_counts": execution_summary.get("scheduler_counts", {}),
                "sqlite_writers": execution_summary.get("sqlite_writers"),
                "execution_modes": execution_summary.get("execution_modes"),
            },
            "relevant_execution_modes": [
                {
                    "id": mode,
                    "profile": execution_modes.get(mode, {}).get("profile"),
                    "scope": execution_modes.get(mode, {}).get("scope"),
                    "heavy_step_policy": execution_modes.get(mode, {}).get("heavy_step_policy"),
                    "claim_boundary": execution_modes.get(mode, {}).get("claim_boundary"),
                    "agent_surface": execution_modes.get(mode, {}).get("agent_surface"),
                }
                for mode in ["watchdog_save_pulse", "daily", "explicit_step", "release_deep"]
                if isinstance(execution_modes.get(mode), dict)
            ],
            "agent_rule": "Use sequential-required steps as settled finalizers; use DAG-safe steps only after dependencies are satisfied.",
            "mcp_tool": "get_pipeline_execution_contract",
            "artifact": "output/.raw/pipeline_execution_contract_validation.json",
        },
        "engine_signal_contract": {
            "summary": {
                "status": signal_summary.get("status"),
                "contracts": signal_summary.get("contracts"),
                "evidence_kinds": signal_summary.get("evidence_kinds"),
            },
            "relevant_contracts": signal_contracts[:6],
            "agent_rule": "ContextOS packets are focus/context signals, not release proof or autonomous mutation approval.",
            "mcp_tool": "get_engine_signal_contracts",
            "artifact": "output/.raw/engine_signal_contract_validation.json",
        },
        "agent_action_directives": build_agent_action_directives(
            signals_data,
            audit_report=audit_report,
        quality_gate=quality_gate,
        priority_pack=priority_pack,
        max_items=8,
        project_scope=project_scope,
    ),
        "l1_focus": visible,
        "l2_halo": deduped_halo[: max_signals * 3],
        "upstream_traces": upstream_traces,
        "recommended_next_steps": [
            "Read relevant_capabilities before deciding which artifacts or validators to trust.",
            "Read architecture_governance_context before interpreting architecture rules or claiming a human seal.",
            "Read capability_activation before deciding which capability family should guide this AI turn.",
            "Read pipeline_execution_contract before changing scheduling or running release-style validations in parallel.",
            "Read engine_signal_contract before treating focus signals as verdicts or action plans.",
            "Read reasoning_breadcrumbs for every L1 focus file.",
            "Run get_test_impact for edited source files before finalizing changes.",
            "Run validate_patch before applying broad AI-generated patches.",
        ],
    }


def escape_markdown_code_blocks(content: str) -> str:
    """Escape triple backticks to avoid prompt formatting escapes."""
    return content.replace("```", r"\`\`\`")


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    if not text:
        return '""'
    if any(ch in text for ch in [":", "#", "{", "}", "[", "]", "\n", "`"]) or text.strip() != text:
        return f'"{text}"'
    return text


def _yaml_lines(value: Any, *, indent: int = 0) -> list[str]:
    pad = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.extend(_yaml_lines(child, indent=indent + 2))
            elif isinstance(child, str) and "\n" in child:
                lines.append(f"{pad}{key}: |-")
                lines.extend(f"{pad}  {line}" for line in child.splitlines())
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar(child)}")
        return lines or [f"{pad}{{}}"]
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.extend(_yaml_lines(item, indent=indent + 2))
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
        return lines or [f"{pad}[]"]
    return [f"{pad}{_yaml_scalar(value)}"]


def _first_directive(packet: dict[str, Any]) -> dict[str, Any]:
    directives = packet.get("agent_action_directives", [])
    if isinstance(directives, list) and directives and isinstance(directives[0], dict):
        return directives[0]
    return {}


def _public_target_refs(directive: dict[str, Any], *, limit: int | None = None) -> list[str]:
    limit = int(limit or contextos_signal_limit("agent_related_files"))
    refs = [
        str(ref).strip()
        for ref in directive.get("target_refs", []) or []
        if str(ref).strip()
    ]
    for row in directive.get("file_context", []) or []:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project_key") or "").strip()
        path = str(row.get("repo_relative_path") or "").strip()
        if project and path:
            refs.append(f"{project}::{path}")
    seen: set[str] = set()
    deduped: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            deduped.append(ref)
    return deduped[:limit]


def _public_target_projects(directive: dict[str, Any], *, limit: int | None = None) -> list[str]:
    limit = int(limit or contextos_signal_limit("agent_projects"))
    projects: list[str] = []
    for row in directive.get("file_context", []) or []:
        if isinstance(row, dict):
            project = str(row.get("project_key") or "").strip()
            if project:
                projects.append(project)
    seen: set[str] = set()
    deduped: list[str] = []
    for project in projects:
        if project not in seen:
            seen.add(project)
            deduped.append(project)
    return deduped[:limit]


def _repo_path_key(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if "::" in text:
        text = text.split("::", 1)[1]
    return text.lstrip("/")


def _dedupe_repo_paths(paths: list[Any], *, limit: int = 10) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for path in paths:
        key = _repo_path_key(path)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
        if len(deduped) >= limit:
            break
    return deduped


def _directive_target_paths(directive: dict[str, Any], source_grounding: dict[str, Any] | None = None) -> list[str]:
    candidates: list[Any] = []
    candidates.extend(directive.get("target_files", []) or [])
    for row in directive.get("file_context", []) or []:
        if isinstance(row, dict):
            candidates.append(row.get("repo_relative_path"))
    if isinstance(source_grounding, dict):
        candidates.append(source_grounding.get("target_file"))
    return _dedupe_repo_paths(candidates, limit=12)


def _trace_matches_targets(trace: dict[str, Any], target_paths: list[str]) -> bool:
    if not target_paths:
        return False
    target_keys = {_repo_path_key(path) for path in target_paths if _repo_path_key(path)}
    trace_keys = {
        _repo_path_key(trace.get("target_file")),
        _repo_path_key(trace.get("target")),
        _repo_path_key(trace.get("target_ref")),
        _repo_path_key(trace.get("node_key")),
    }
    for trace_key in {key for key in trace_keys if key}:
        if trace_key in target_keys:
            return True
        if any(target_key.endswith(f"/{trace_key}") or trace_key.endswith(f"/{target_key}") for target_key in target_keys):
            return True
    return False


def _matching_upstream_trace(packet: dict[str, Any], directive: dict[str, Any], source_grounding: dict[str, Any]) -> dict[str, Any]:
    target_paths = _directive_target_paths(directive, source_grounding)
    upstream_traces = packet.get("upstream_traces", []) if isinstance(packet, dict) else []
    if not isinstance(upstream_traces, list):
        return {}
    for trace in upstream_traces:
        if isinstance(trace, dict) and _trace_matches_targets(trace, target_paths):
            return trace
    return {}


def _surgical_inspect_first(directive: dict[str, Any], trace: dict[str, Any], source_grounding: dict[str, Any]) -> list[str]:
    target_projects = _public_target_projects(directive)
    default_project = target_projects[0] if target_projects else str(source_grounding.get("target_project") or "MAIN")
    target_paths = _directive_target_paths(directive, source_grounding)
    candidates: list[Any] = []
    candidates.extend(target_paths)
    candidates.extend(directive.get("related_files", []) or [])
    if trace:
        if isinstance(trace.get("upstream_dependency_files"), list):
            for path in trace.get("upstream_dependency_files", [])[:8]:
                path_key = _repo_path_key(path)
                canonical_target = next(
                    (
                        target_path
                        for target_path in target_paths
                        if path_key == _repo_path_key(target_path)
                        or _repo_path_key(target_path).endswith(f"/{path_key}")
                        or path_key.endswith(f"/{_repo_path_key(target_path)}")
                    ),
                    "",
                )
                if canonical_target:
                    candidates.append(canonical_target)
                    continue
                context = _agent_file_context(str(path), default_project=default_project)
                candidates.append(context.get("repo_relative_path") or path)
    return _dedupe_repo_paths(candidates, limit=10)


def _public_patch_action(directive: dict[str, Any]) -> str:
    intent = str(directive.get("intent") or "").strip()
    if not bool(directive.get("mutation_proposed")):
        return (
            "Inspect the bounded evidence and related files only; do not edit from this attention signal "
            "without a separate actionable finding or explicit user intent."
        )
    if intent == "fix_architecture_violation":
        return "Open the target file, find the cited implementation pattern, and change the smallest code region that satisfies the issue."
    if intent == "inspect_hot_file_and_blast_radius":
        return "Inspect the target file first, then inspect related files only when the symptom points across the listed dependency boundary."
    if intent == "review_quality_attention":
        return "Treat this as a review task first; only edit after identifying a concrete target file and code pattern."
    if intent == "review_priority_repository_finding":
        return "Open the target and related files, compare the reported surfaces, then either make the smallest consolidation patch or ask for human direction."
    if intent == "no_immediate_code_patch":
        return "Do not edit yet; ask for a concrete target or inspect a specific file/symbol first."
    return "Inspect the target files before editing, then make the smallest safe repository change."


def _public_evidence(value: Any, *, limit: int = 700) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    hidden_markers = ("output/.raw/", "config/", "tools/", "ContextOS", "atlas", "SAGE")
    if any(marker in text for marker in hidden_markers):
        return None
    return text[:limit]


def _public_engineering_principles(directive: dict[str, Any], *, limit: int = 2) -> list[dict[str, Any]]:
    intent = str(directive.get("intent") or "agent_directive").strip()
    contexts = [intent, str(directive.get("rule") or "").strip(), "agent_directive"]
    selected: dict[str, dict[str, Any]] = {}
    for context in contexts:
        if not context:
            continue
        for principle in principles_for_context(context, limit=limit):
            principle_id = str(principle.get("id") or "")
            if principle_id and principle_id not in selected:
                selected[principle_id] = principle
            if len(selected) >= limit:
                return list(selected.values())
    return list(selected.values())[:limit]


def render_surgical_operation_brief(packet: dict[str, Any], *, debug: bool = False) -> str:
    """Render a compact target-repository brief from the canonical JSON packet."""
    mission = packet.get("mission_brief", []) if isinstance(packet, dict) else []
    directive = _first_directive(packet)
    architecture = packet.get("architecture_governance_context", {}) if isinstance(packet, dict) else {}
    summary = packet.get("summary", {}) if isinstance(packet, dict) else {}
    source_grounding = packet.get("source_grounding") if isinstance(packet.get("source_grounding"), dict) else {}
    operation_trace = packet.get("operation_trace") if isinstance(packet.get("operation_trace"), dict) else {}
    trace = _matching_upstream_trace(packet, directive, source_grounding)
    actionability = str(directive.get("actionability") or "orientation_only").strip().lower()
    mutation_proposed = bool(directive.get("mutation_proposed")) and actionability == "actionable_proposal"

    seal_state = str(architecture.get("seal_state") or "UNKNOWN")
    inspect_first = _surgical_inspect_first(directive, trace, source_grounding)
    explanation = directive.get("rule_explanation") or {}
    issue_title = explanation.get("label") or str(directive.get("rule") or "Targeted repository issue").replace("_", " ").title()
    why_it_matters = explanation.get("rationale")
    target_refs = _public_target_refs(directive)
    target_projects = _public_target_projects(directive)
    if directive.get("rule") == "no_active_contextos_or_audit_action":
        issue_title = "No targeted repository change requested"
        why_it_matters = "No current repository evidence is asking for a specific code patch."
    validation_commands = directive.get("validation_commands", [])
    public_evidence = _public_evidence(directive.get("evidence"))
    public_principles = _public_engineering_principles(directive)
    if not inspect_first:
        inspect_first = _dedupe_repo_paths((directive.get("target_files", []) or []) + (directive.get("related_files", []) or []))
    validation_commands = [str(item) for item in validation_commands if str(item).strip()]
    validation_policy = target_repo_validation_policy()
    validation_tools = directive.get("validation_tools") if isinstance(directive.get("validation_tools"), list) else []
    if not validation_tools:
        validation_tools = validation_policy.get("validation_tools", [])
    mission_lines = (
        [
            "Make the smallest safe change proposed by the actionable repository evidence.",
            "Stay inside the listed files unless the user explicitly expands scope.",
        ]
        if mutation_proposed
        else [
            "Inspect and verify the bounded repository evidence without mutating from this packet alone.",
            "Hotness, dependency-halo membership, or absent approval requirements do not authorize a code change.",
        ]
    )
    brief_yaml = {
        "mission": mission_lines,
        "task": {
            "analysis_root": packet.get("analysis_root") or "",
            "target_projects": target_projects,
            "target_files": directive.get("target_files", []),
            "target_refs": target_refs,
            "related_files": directive.get("related_files", []),
            "issue": issue_title,
            "why_it_matters": why_it_matters,
            "rule_mode": directive.get("rule_mode") or explanation.get("mode") or "unknown",
            "human_approval_required": directive.get("human_approval_required", False),
            "approval_decision_source": directive.get("approval_decision_source") or "unknown",
            "approval_reason": directive.get("approval_reason") or "Approval rationale is unavailable; refresh the target-repository packet before mutation.",
            "actionability": actionability,
            "mutation_proposed": mutation_proposed,
            "mutation_authority": directive.get("mutation_authority") or "not_granted_by_this_directive",
            "approval_is_not_mutation_authority": True,
            "approval_requirement_scope": directive.get("approval_requirement_scope")
            or "not_applicable_without_separate_actionable_mutation",
        },
        "path_contract": {
            "open_files_with": "analysis_root + target_files or related_files",
            "target_refs_usage": "SAGE/MCP follow-up references only; not filesystem paths",
            "target_ref_format": "<project>::<repo_relative_path>; MAIN is the primary analyzed project scope",
        },
        "do": [_public_patch_action(directive)],
        "inspect_first": inspect_first,
        "downstream_check": {
            "direct_dependents": trace.get("direct_dependent_files", [])[:8] if isinstance(trace.get("direct_dependent_files"), list) else [],
        },
        "verify_with_sage": [
            "Run validate_patch(target_file, patch_content) before applying broad changes.",
            "Run get_test_impact(target_file) for edited files before finalizing.",
        ],
        "validation": {
            "mode": validation_policy.get("mode"),
            "tools": validation_tools,
            "commands": validation_commands[:6],
            "command_contracts": command_contracts_for_agent(validation_commands, limit=6),
            "completion_rule": validation_policy.get("completion_rule"),
        },
        "do_not": [
            "Do not invent cleanup work from broad summaries.",
            "Do not refactor unrelated files outside target_files, related_files, or inspect_first.",
            "Do not add allowlists, suppressions, deletions, moves, or broad rewrites without human approval.",
        ],
    }
    if mutation_proposed:
        brief_yaml["do"].extend(
            [
                "Change only the code needed to satisfy the issue and evidence.",
                "Ask before expanding scope beyond the listed files.",
            ]
        )
    else:
        brief_yaml["do"].append(
            "Obtain a concrete actionable finding or explicit user intent before proposing repository mutation."
        )

    projections = packet.get("dependency_projections") if isinstance(packet.get("dependency_projections"), list) else []
    directive_refs = set(_public_target_refs(directive))
    dependency_projection = next(
        (
            row
            for row in projections
            if isinstance(row, dict) and str(row.get("target_ref") or "") in directive_refs
        ),
        {},
    )
    if dependency_projection:
        dependent_files = dependency_projection.get("direct_dependents")
        dependent_files = dependent_files if isinstance(dependent_files, list) else []
        shown_files = dependent_files[:8]
        total = int(dependency_projection.get("direct_dependents_count") or len(dependent_files))
        brief_yaml["downstream_check"] = {
            "direct_dependents_total": total,
            "direct_dependents_shown": len(shown_files),
            "direct_dependents_omitted": max(0, total - len(shown_files)),
            "direct_dependents": shown_files,
            "dependency_graph_source": dependency_projection.get("dependency_graph_source") or "unknown",
            "dependency_snapshot_id": dependency_projection.get("dependency_snapshot_id") or "",
        }
    if source_grounding:
        brief_yaml["source_grounding"] = source_grounding
        snippets = (
            source_grounding.get("target_source_snippets")
            if isinstance(source_grounding.get("target_source_snippets"), list)
            else []
        )
        partial_snippets = [
            snippet
            for snippet in snippets
            if isinstance(snippet, dict)
            and str(snippet.get("snippet_status") or "").startswith("partial_included")
        ]
        has_partial_snippet = bool(partial_snippets)
        if has_partial_snippet:
            follow_up = str(partial_snippets[0].get("follow_up_if_needed") or "").strip()
            if follow_up:
                follow_up_before_patch = follow_up.replace(
                    " only if you need to inspect that omitted body chunk before editing outside the shown boundary snippet.",
                    " before patching this large symbol; use it to choose the smallest safe edit span.",
                )
                brief_yaml["do"].insert(
                    0,
                    "This brief is not one-shot patch-ready; first run follow_up_before_patch to choose the smallest safe edit span.",
                )
                brief_yaml["follow_up_before_patch"] = follow_up_before_patch
            brief_yaml["do_not"].append(
                "Do not edit omitted source lines from a partial snippet alone; request a narrower inspect_file/inspect_symbol span first."
            )
    if operation_trace:
        brief_yaml["operation_trace"] = operation_trace
    if public_evidence:
        brief_yaml["task"]["evidence"] = public_evidence
    if public_principles:
        brief_yaml["engineering_principles"] = public_principles
    if isinstance(directive.get("evidence_boundary"), dict):
        brief_yaml["task"]["evidence_boundary"] = directive["evidence_boundary"]
    brief_yaml["context_budget"] = context_budget_profile(json.dumps(brief_yaml, ensure_ascii=False))
    if debug:
        brief_yaml["debug_internal_refs"] = {
            "atlas_nodes": directive.get("atlas_nodes", []),
            "file_context": directive.get("file_context", []),
            "source_artifacts": directive.get("source_artifacts", []),
            "seal_state": seal_state,
            "oracle_status": architecture.get("oracle_status"),
            "architecture_profile": architecture.get("top_recommended_profile"),
            "rule_id": directive.get("rule"),
            "intent": directive.get("intent"),
            "raw_action": directive.get("action"),
            "raw_evidence": directive.get("evidence"),
            "validation_commands": validation_commands,
        }
    usage_line = (
        "Use this brief to make the smallest evidence-backed repository change."
        if mutation_proposed
        else "Use this brief for bounded repository orientation; it does not propose or authorize a mutation."
    )
    lines = [
        "# Repository Surgical Brief",
        "",
        usage_line,
        "",
        "```yaml",
        *_yaml_lines(brief_yaml),
        "```",
    ]
    if debug:
        lines.extend(
            [
                "",
                "## Debug Summary",
                "",
                f"- returned_focus_files: `{summary.get('returned_focus_files', 0)}`",
                f"- returned_halo_files: `{summary.get('returned_halo_files', 0)}`",
                f"- scene_pivot: `{summary.get('is_scene_pivot', False)}`",
                f"- react_edge_failed_checks: `{(summary.get('react_edge_case_gate') or {}).get('failed_checks', 0)}`",
                "",
                "## Source Of Truth",
                "",
                "- The canonical machine packet stays in JSON for SAGE validators and MCP internals.",
                "- This Markdown/YAML view is a token-bounded projection for coding agents.",
            ]
        )
    return "\n".join(lines)


def render_active_signals(
    data: dict[str, Any],
    *,
    output_format: str = "markdown",
    include_bodies: bool = False,
    max_files: int = 8,
    max_chars_per_file: int = 6000,
    scope: str = "summary",
    resolve_absolute_path: Callable[[str, str], Path],
) -> str:
    active_signals = [sanitize_signal(sig) for sig in data.get("active_signals", []) if isinstance(sig, dict)]
    meta = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}
    source_mode = str(meta.get("source_mode") or "unknown")
    current_change_scope = str(
        meta.get("current_change_scope")
        or ("bounded" if source_mode == "direct_pipeline_scope" else "unknown")
    )
    current_turn_claim = str(
        meta.get("current_turn_claim")
        or ("supported" if current_change_scope == "bounded" else "not_established")
    )
    signal_origin = str(meta.get("signal_origin") or source_mode)
    rejected_files = data.get("summary", {}).get("rejected_files", {})
    managed_projection_count = (
        int(rejected_files.get("managed_projection") or 0)
        if isinstance(rejected_files, dict)
        else 0
    )
    if not active_signals:
        if output_format == "json":
            return json.dumps(
                {
                    "status": "no_active_signals",
                    "analysis_root": data.get("analysis_root") or "",
                    "current_change_scope": current_change_scope,
                    "current_turn_claim": current_turn_claim,
                    "signal_origin": signal_origin,
                    "active_signals": [],
                    "managed_projection_changes_excluded": managed_projection_count,
                    "agent_directive": "Do not edit from ContextOS alone. Search or inspect a concrete target first.",
                },
                indent=2,
                ensure_ascii=False,
            )
        brief_yaml = {
            "mission": [
                "Do not make a repository edit from ContextOS alone right now.",
            ],
            "task": {
                "status": "no_active_signals",
                "analysis_root": data.get("analysis_root") or "",
                "current_change_scope": current_change_scope,
                "current_turn_claim": current_turn_claim,
                "signal_origin": signal_origin,
                "target_files": [],
                "managed_projection_changes_excluded": managed_projection_count,
                "why_it_matters": (
                    "No actionable modified files remain after managed projections were excluded."
                    if managed_projection_count
                    else (
                        "No active signal exists in the bounded current change scope."
                        if current_turn_claim == "supported"
                        else "The latest persisted projection contains no active signal; current-turn scope is not established."
                    )
                ),
            },
            "do": [
                "Use explicit user intent, search_symbols, inspect_file, or inspect_symbol to choose a concrete target before editing.",
            ],
            "do_not": [
                "Do not invent cleanup work from a broad or empty context window.",
                "Do not treat missing active signals as proof that the repository has no risk.",
            ],
        }
        if managed_projection_count:
            brief_yaml["do"].append(
                "Use the managed projection's owning guarded synchronization workflow if distribution alignment is required."
            )
            brief_yaml["do_not"].append(
                "Do not edit excluded managed projection files directly."
            )
        brief_yaml["context_budget"] = context_budget_profile(json.dumps(brief_yaml, ensure_ascii=False))
        empty_scope_message = (
            "No bounded current ContextOS signal is asking for a targeted patch."
            if current_turn_claim == "supported"
            else "The latest persisted ContextOS projection contains no active signal; current-turn scope is unknown."
        )
        return "\n".join(
            [
                "# ContextOS: No Active Surgery Signals",
                "",
                empty_scope_message,
                "",
                "```yaml",
                *_yaml_lines(brief_yaml),
                "```",
            ]
        )

    max_files = max(0, min(int(max_files or 0), 25))
    max_chars_per_file = max(0, min(int(max_chars_per_file or 0), 20000))
    scope = str(scope or "summary").lower()
    if scope not in {"summary", "l1", "l2", "full"}:
        scope = "summary"
    visible_signals = active_signals[:max_files] if max_files else []
    omitted_l1 = max(0, len(active_signals) - len(visible_signals))
    total_dependents = sum(len(sig.get("direct_dependents", [])) for sig in active_signals)
    focus_files = [
        str(sig.get("relative_path") or "").strip()
        for sig in visible_signals
        if str(sig.get("relative_path") or "").strip()
    ]
    target_refs = [
        str(sig.get("target_ref") or "").strip()
        for sig in visible_signals
        if str(sig.get("target_ref") or "").strip()
    ]
    path_contract = {
        "analysis_root": data.get("analysis_root") or "",
        "open_files_with": "analysis_root + target_files",
        "target_ref_usage": "SAGE/MCP follow-up references only; not filesystem paths",
        "target_ref_format": "<project>::<repo_relative_path>; MAIN is the primary analyzed project scope",
    }
    sanitized = {
        "meta": meta,
        "summary": data.get("summary", {}),
        "input_evidence": data.get("input_evidence", {}),
        "analysis_root": data.get("analysis_root") or "",
        "path_contract": path_contract,
        "target_files": focus_files,
        "target_refs": target_refs,
        "changed_files_count": len(active_signals),
        "total_direct_dependents": total_dependents,
        "returned_l1_count": len(visible_signals),
        "omitted_l1_count": omitted_l1,
        "include_bodies": bool(include_bodies),
        "scope": scope,
        "current_change_scope": current_change_scope,
        "current_turn_claim": current_turn_claim,
        "signal_origin": signal_origin,
        "active_signals": visible_signals,
    }
    if output_format == "json":
        return json.dumps(sanitized, indent=2, ensure_ascii=False)

    scope_description = (
        "Deterministic bounded focus window for the current change scope."
        if current_change_scope == "bounded" and current_turn_claim == "supported"
        else "Latest persisted ContextOS orientation; the current AI-turn change scope is unknown."
    )
    lines = [
        "# ContextOS: Active Surgery Signals",
        scope_description + "\n",
        "## Summary Profile",
        f"- **Active Focus Files (L1):** {len(active_signals)}",
        f"- **Volatile Neighbors (L2 Direct):** {total_dependents}",
        f"- **Halo Files:** {data.get('summary', {}).get('halo_files', 0)}",
        f"- **Scene Pivot:** `{data.get('summary', {}).get('is_scene_pivot', False)}`",
        f"- **Returned L1 Files:** {len(visible_signals)}",
        f"- **Omitted L1 Files:** {omitted_l1}",
        f"- **Managed Projection Changes Excluded:** {managed_projection_count}",
        f"- **Bodies Included:** {bool(include_bodies)}",
        f"- **Scope:** `{scope}`\n",
        f"- **Current Change Scope:** `{current_change_scope}`",
        f"- **Current Turn Claim:** `{current_turn_claim}`",
        f"- **Signal Origin:** `{signal_origin}`\n",
    ]
    focus_yaml = {
        "path_contract": path_contract,
        "target_files": focus_files,
        "target_refs": target_refs,
        "current_change_scope": current_change_scope,
        "current_turn_claim": current_turn_claim,
        "signal_origin": signal_origin,
        "managed_projection_changes_excluded": managed_projection_count,
        "next_action": "Inspect one listed target_file or call get_surgical_operation_packet before editing.",
        "do": [
            "Inspect one listed target_file or call get_surgical_operation_packet before editing.",
            "Use this as a bounded focus window, not as proof of repository-wide health.",
        ],
        "do_not": [
            "Do not infer repository-wide safety from a bounded ContextOS focus window.",
            "Do not edit files outside target_files without inspecting the relevant halo or impact radius first.",
        ],
    }
    if current_turn_claim != "supported":
        focus_yaml["do"].insert(
            0,
            "Confirm the current changed-file set before treating these persisted signals as task-local focus.",
        )
        focus_yaml["do_not"].insert(
            0,
            "Do not describe these signals as files currently modified in this AI turn.",
        )
    if managed_projection_count:
        focus_yaml["do_not"].append(
            "Do not edit excluded managed projection files directly; use their owning guarded workflow."
        )
    focus_yaml["context_budget"] = context_budget_profile(json.dumps(focus_yaml, ensure_ascii=False))
    if scope == "summary":
        lines.extend([
            "## Agent Focus Packet",
            "",
            "```yaml",
            *_yaml_lines(focus_yaml),
            "```",
        ])
        return "\n".join(lines)

    lines.extend([
        "## Agent Focus Packet",
        "",
        "```yaml",
        *_yaml_lines(focus_yaml),
        "```",
    ])

    lines.extend([
        "---",
        (
            "## L1 Focus (Bounded Changed Files)"
            if current_turn_claim == "supported"
            else "## L1 Focus (Persisted Orientation Candidates)"
        ),
        (
            "These files came from the bounded current change scope. Review their blast radius and active doctrine violations.\n"
            if current_turn_claim == "supported"
            else "These files came from the latest persisted signal generation; confirm the current change set before using them as task-local evidence.\n"
        ),
    ])
    l2_nodes_to_read: list[str] = []
    for sig in visible_signals:
        node_key = sig.get("node_key", "MAIN::")
        rel_path = sig.get("relative_path", "")
        direct_deps = sig.get("direct_dependents", [])
        transitive_deps = sig.get("transitive_dependents", [])
        direct_count = int(sig.get("direct_dependents_count") or len(direct_deps))
        transitive_count = int(sig.get("transitive_dependents_count") or len(transitive_deps))
        direct_omitted = int(sig.get("direct_dependents_omitted") or 0)
        transitive_omitted = int(sig.get("transitive_dependents_omitted") or 0)
        for dep in direct_deps:
            if dep not in l2_nodes_to_read:
                l2_nodes_to_read.append(dep)
        lines.append(f"### `{node_key}`")
        lines.append(f"- **Relative Path:** `{rel_path}`")
        lines.append(f"- **Target Ref:** `{sig.get('target_ref', '')}`")
        lines.append(f"- **Signal Kind:** `{sig.get('signal_kind', 'source')}`")
        lines.append(
            f"- **Impact Score:** `{sig.get('impact_score')}` "
            f"(status: `{sig.get('impact_score_status', 'not_declared')}`)"
        )
        lines.append(f"- **Risk Claim Boundary:** `{sig.get('risk_claim_boundary', 'not_declared')}`")
        lines.append(
            f"- **Direct Dependents:** `{direct_count}` total "
            f"(`{len(direct_deps)}` shown, `{direct_omitted}` omitted)"
        )
        lines.append(
            f"- **Transitive Dependents:** `{transitive_count}` total "
            f"(`{len(transitive_deps)}` shown, `{transitive_omitted}` omitted)"
        )
        lines.append(
            f"- **Dependency Projection:** `{sig.get('dependency_projection_status', 'signal_projection')}` "
            f"from `{sig.get('dependency_graph_source', 'signals')}` "
            f"(snapshot `{sig.get('dependency_snapshot_id', 'not_declared')}`)"
        )
        breadcrumbs = sig.get("reasoning_breadcrumbs", [])
        if breadcrumbs:
            lines.append("- **Reasoning Breadcrumbs:**")
            for breadcrumb in breadcrumbs[:6]:
                lines.append(f"  - {breadcrumb}")
        halo = sig.get("focus_halo", [])
        if halo:
            lines.append("- **Focus Halo:**")
            for item in halo[:3]:
                if isinstance(item, dict):
                    lines.append(
                        f"  - `{item.get('node_key')}` "
                        f"(rank {item.get('halo_rank')}, score {item.get('impact_score', 0.0)})"
                    )
        violations = sig.get("active_violations", [])
        if violations:
            lines.append("- **Active Governance Violations:**")
            for violation in violations:
                lines.append(f"  - `[{violation.get('rule')}]` (Severity: {violation.get('severity')}) - {violation.get('message')}")
        else:
            lines.append("- **Active Governance Violations:** None")
        cycles = sig.get("circular_cycles", [])
        cycle_status = str(sig.get("circular_cycles_status") or "not_declared")
        if cycles and cycle_status == "scoped_scc_membership":
            lines.append("- **Scoped Strongly Connected Component Cycle Witness:**")
            for witness in cycles:
                lines.append(f"  - witness: `{' -> '.join(witness)}`")
            lines.append(
                "- **Canonical Circular Dependency Proof:** deferred; "
                "the witness proves current Atlas SCC membership but is not a full cycle enumeration."
            )
        elif cycles:
            lines.append("- **Circular Dependency Cycles:**")
            for cycle in cycles:
                lines.append(f"  - `{' -> '.join(cycle)}`")
        elif cycle_status == "deferred_broad_proof":
            lines.append("- **Circular Dependency Proof:** deferred; this live signal makes no cycle-absence claim.")
        elif cycle_status == "scoped_scc_membership":
            lines.append("- **Scoped SCC Membership:** not a member in the current global Atlas projection.")
            lines.append("- **Canonical Circular Dependency Proof:** deferred; full cycle enumeration was not run.")
        elif cycle_status == "available":
            lines.append("- **Circular Dependency Cycles:** none in the declared current evidence.")
        else:
            lines.append("- **Circular Dependency Proof:** not available; cycle state is unknown.")
        if include_bodies:
            proj_key = node_key.split("::", 1)[0] if "::" in node_key else "MAIN"
            body_payload = safe_signal_body(rel_path, proj_key, max_chars_per_file, resolve_absolute_path)
            lines.append("\n#### File Body:")
            if body_payload["masked"]:
                lines.append("- **Masking:** applied")
            if body_payload["truncated"]:
                lines.append("- **Truncated:** true")
            escaped_body = escape_markdown_code_blocks(body_payload['body'])
            lines.append(f"```{body_payload['language']}\n{escaped_body}\n```\n")

    if scope == "l1":
        return "\n".join(lines)

    lines.extend([
        "---",
        "## L2 Direct Dependents (Affected Neighbors)",
        "These files import your active files directly. Verify changes here to prevent regressions.\n",
    ])
    if l2_nodes_to_read:
        for node in sorted(l2_nodes_to_read)[:max_files]:
            proj_key, rel_path = node.split("::", 1) if "::" in node else ("MAIN", node)
            lines.append(f"### `{node}`")
            if include_bodies and scope in {"l2", "full"}:
                body_payload = safe_signal_body(rel_path, proj_key, max_chars_per_file, resolve_absolute_path)
                lines.append("\n#### File Body:")
                if body_payload["masked"]:
                    lines.append("- **Masking:** applied")
                if body_payload["truncated"]:
                    lines.append("- **Truncated:** true")
                escaped_body = escape_markdown_code_blocks(body_payload['body'])
                lines.append(f"```{body_payload['language']}\n{escaped_body}\n```\n")
        omitted_l2 = max(0, len(l2_nodes_to_read) - max_files)
        if omitted_l2:
            lines.append(f"\n_Omitted {omitted_l2} additional L2 nodes due to max_files limit._")
    else:
        lines.append("No active L2 dependents affected by current changes.")
    return "\n".join(lines)
