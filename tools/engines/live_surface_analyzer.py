from __future__ import annotations

from collections import Counter
from pathlib import Path
import difflib

from tools.core.artifact_validator import ensure_valid_payload
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.atlas_io import load_atlas_data
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.projects_registry import project_display_name
from tools.core.report_index import load_oracle_reports
from tools.core.source_snapshot_reader import load_source_text
from tools.core.workspace_mode import get_project_role


RAW_OUTPUT = RAW_DIR / "live_surface_findings.json"
REPORT_OUTPUT = REPORTS_DIR / "live_surface_findings.md"
PRIORITY_RAW_OUTPUT = RAW_DIR / "live_surface_priority_pack.json"
PRIORITY_REPORT_OUTPUT = REPORTS_DIR / "live_surface_priority_pack.md"

TEST_MARKERS = ("test", "tests", "__tests__", "spec", "__mocks__", "stories", "story")
GENERATED_MARKERS = (
    "generated",
    "fixture",
    "fixtures",
    "example",
    "examples",
    "template",
    "templates",
    "demo",
    "registry",
    "mirror",
    "docs",
    "sandbox",
)
CAMPAIGN_MARKERS = ("launchweek", "surveyresults", "campaign", "edition", "promo", "event")
HIGH_CONFIDENCE_ORACLE_CLASSES = {
    "deep_tsc_error",
    "deep_tsc_parser_error",
    "environment_type_error",
    "jsx_intrinsic_contract_error",
    "legacy_src_alias_mismatch",
    "malformed_jsx_token",
    "missing_declared_dependency_in_sanctuary",
    "missing_package_declaration",
    "module_augmentation_error",
    "prop_contract_error",
    "relocated_alias_candidate",
    "type_contract_error",
    "typescript_strictness_error",
}
LOW_SIGNAL_SEGMENTS = (
    "types",
    "constants",
    "contexts",
    "schemas",
    "registry",
    "registries",
    "service-worker",
)
LOW_SIGNAL_STEMS = {
    "index",
    "types",
    "constants",
    "schema",
    "schemas",
    "registry",
    "modalregistry",
    "service-worker",
    "sw",
}


def _normalize_path(value: str) -> str:
    return str(value or "").replace("\\", "/").strip("/")


def _parse_clone_instance(instance_id: str) -> dict | None:
    parts = str(instance_id or "").split("::", 2)
    if len(parts) != 3:
        return None
    return {"project": parts[0], "file": _normalize_path(parts[1]), "symbol": parts[2]}


def _build_dead_index(dead_payload: dict) -> tuple[set[str], set[str]]:
    dead_scoped_files: set[str] = set()
    dead_scoped_symbols: set[str] = set()
    for item in dead_payload.get("items", []) if isinstance(dead_payload, dict) else []:
        if not isinstance(item, dict):
            continue
        scoped_file = str(item.get("scoped_file") or "").strip()
        project = str(item.get("project") or "").strip()
        file_path = _normalize_path(item.get("file") or "")
        symbol = str(item.get("symbol") or "").strip()
        if scoped_file:
            dead_scoped_files.add(scoped_file)
            if symbol:
                dead_scoped_symbols.add(f"{scoped_file}::{symbol}")
        elif project and file_path:
            scoped = f"{project}::{file_path}"
            dead_scoped_files.add(scoped)
            if symbol:
                dead_scoped_symbols.add(f"{scoped}::{symbol}")
    return dead_scoped_files, dead_scoped_symbols


def _atlas_file_info(atlas: dict, project: str, file_path: str) -> dict:
    project_payload = atlas.get(project, {}) if isinstance(atlas, dict) else {}
    files = project_payload.get("files", {}) if isinstance(project_payload, dict) else {}
    info = files.get(file_path, {}) if isinstance(files, dict) else {}
    return info if isinstance(info, dict) else {}


def _project_root(atlas: dict, project: str) -> Path | None:
    project_payload = atlas.get(project, {}) if isinstance(atlas, dict) else {}
    project_info = project_payload.get("project", {}) if isinstance(project_payload, dict) else {}
    root = project_info.get("root") if isinstance(project_info, dict) else None
    if not root:
        return None
    return Path(str(root))


def _absolute_paths(atlas: dict, instances: list[dict]) -> list[str] | None:
    paths: list[str] = []
    for item in instances:
        root = _project_root(atlas, item["project"])
        if root is None:
            return None
        paths.append(str((root / item["file"]).resolve()))
    return paths


def _content_similarity(atlas: dict, instances: list[dict]) -> tuple[float | None, bool]:
    if len(instances) < 2:
        return None, False
    texts = []
    absolute_paths = _absolute_paths(atlas, instances)
    if absolute_paths is None:
        return None, False
    for item, abs_path_raw in zip(instances, absolute_paths):
        abs_path = Path(abs_path_raw)
        content = load_source_text(
            item["project"],
            item["file"],
            fallback_path=abs_path,
            component="live_surface_analyzer",
        )
        if not content:
            return None, False
        texts.append(content)
    same_path = len(set(absolute_paths)) < len(absolute_paths)
    pair_scores = []
    for idx, left in enumerate(texts):
        for right in texts[idx + 1:]:
            pair_scores.append(difflib.SequenceMatcher(None, left, right).ratio())
    if not pair_scores:
        return None, same_path
    return round(min(pair_scores), 3), same_path


def _duplicate_subtype(content_similarity: float | None, *, cross_variation: bool, includes_main: bool) -> str | None:
    if content_similarity is None:
        return None
    if content_similarity >= 0.999:
        return "exact_content_duplicate"
    if content_similarity >= 0.95 and (cross_variation or includes_main):
        return "near_duplicate_import_path_variation"
    if content_similarity >= 0.95:
        return "near_duplicate_same_project_surface"
    return "semantic_near_duplicate"


def _setify(value) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        result.update(str(k) for k in value.keys() if str(k).strip())
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                candidate = item.get("name") or item.get("symbol") or item.get("import") or item.get("path")
                if candidate:
                    result.add(str(candidate))
            elif item:
                result.add(str(item))
    elif value:
        result.add(str(value))
    return result


def _path_tags(file_path: str) -> set[str]:
    lower = _normalize_path(file_path).lower()
    tags: set[str] = set()
    if any(marker in lower for marker in TEST_MARKERS):
        tags.add("test")
    if any(marker in lower for marker in GENERATED_MARKERS):
        tags.add("generated")
    if any(marker in lower for marker in CAMPAIGN_MARKERS):
        tags.add("campaign")
    return tags


def _root_segment(file_path: str) -> str:
    normalized = _normalize_path(file_path)
    return normalized.split("/", 1)[0].lower() if normalized else ""


def _low_signal_surface(file_path: str) -> bool:
    normalized = _normalize_path(file_path).lower()
    if not normalized:
        return False
    stem = Path(normalized).stem.lower()
    if stem in LOW_SIGNAL_STEMS:
        return True
    if normalized.endswith(".d.ts"):
        return True
    parts = [part for part in normalized.split("/") if part]
    return any(part in LOW_SIGNAL_SEGMENTS for part in parts)


def _is_cross_variation_cluster(projects: set[str]) -> bool:
    normalized = {str(project).strip().upper() for project in projects if str(project).strip()}
    return len(normalized) > 1 and "MAIN" not in normalized


def _responsibility_alignment(atlas: dict, instances: list[dict]) -> tuple[float, dict]:
    if len(instances) < 2:
        return 0.0, {}
    infos = [_atlas_file_info(atlas, item["project"], item["file"]) for item in instances]
    stems = {Path(item["file"]).stem.lower() for item in instances}
    exports = [_setify(info.get("exports", [])) for info in infos]
    imports = [_setify(info.get("imports", [])) | _setify(info.get("import_records", [])) for info in infos]
    features = [_setify(info.get("features", [])) for info in infos]
    themes = [_setify(info.get("themes", [])) for info in infos]
    symbols = [_setify(info.get("symbols", [])) | ({item["symbol"]} if item.get("symbol") else set()) for info, item in zip(infos, instances)]

    checks = {
        "same_stem": len(stems) == 1,
        "export_overlap": bool(exports and set.intersection(*exports)) if len(exports) > 1 else False,
        "import_overlap": bool(imports and set.intersection(*imports)) if len(imports) > 1 else False,
        "feature_overlap": bool(features and set.intersection(*features)) if len(features) > 1 else False,
        "theme_overlap": bool(themes and set.intersection(*themes)) if len(themes) > 1 else False,
        "symbol_overlap": bool(symbols and set.intersection(*symbols)) if len(symbols) > 1 else False,
    }
    score = round(sum(1 for passed in checks.values() if passed) / max(len(checks), 1), 3)
    return score, checks


def _classify_duplicate_cluster(atlas: dict, cluster: dict, instances: list[dict]) -> tuple[str, str, str, dict]:
    projects = {item["project"] for item in instances}
    roles = {get_project_role(project) for project in projects}
    roots = {_root_segment(item["file"]) for item in instances if _root_segment(item["file"])}
    normalized_projects = {str(project).strip().upper() for project in projects if str(project).strip()}
    includes_main = "MAIN" in normalized_projects
    cross_variation = _is_cross_variation_cluster(projects)
    tags = set()
    for item in instances:
        tags.update(_path_tags(item["file"]))
    alignment_score, alignment_flags = _responsibility_alignment(atlas, instances)
    absolute_paths = _absolute_paths(atlas, instances) or []
    same_absolute_path = len(set(absolute_paths)) < len(absolute_paths) if absolute_paths else False
    content_similarity: float | None = None
    exact_two_way = len(instances) == 2 and len(projects) <= 2
    strong_alignment = alignment_score >= 0.67
    low_signal = all(_low_signal_surface(item["file"]) for item in instances)

    evidence = {
        "cluster_id": cluster.get("cluster_id"),
        "lines": int(cluster.get("lines", 0) or 0),
        "distinct_files": len({_normalize_path(item["file"]) for item in instances}),
        "hash": cluster.get("hash"),
        "cross_project": len(projects) > 1,
        "cross_variation": cross_variation,
        "includes_main": includes_main,
        "project_roles": sorted(roles),
        "root_segments": sorted(roots),
        "responsibility_alignment_score": alignment_score,
        "responsibility_alignment": alignment_flags,
        "content_similarity": content_similarity,
        "same_absolute_path": same_absolute_path,
        "path_tags": sorted(tags),
        "low_signal_surface": low_signal,
    }

    if same_absolute_path:
        return ("redundant_surface", "LOW", "low", evidence)

    if "test" in tags or "generated" in tags or "campaign" in tags:
        return ("redundant_surface", "LOW", "low", evidence)

    if len(roots) > 1:
        return ("redundant_surface", "LOW", "low", evidence)

    if len(projects) > 1:
        if cross_variation:
            risk = "low"
            confidence = "LOW" if not strong_alignment else "MEDIUM"
            return ("redundant_surface", confidence, risk, evidence)
        if includes_main:
            if strong_alignment and exact_two_way:
                content_similarity, same_absolute_path = _content_similarity(atlas, instances)
                evidence["content_similarity"] = content_similarity
                evidence["same_absolute_path"] = same_absolute_path
                evidence["duplicate_subtype"] = _duplicate_subtype(
                    content_similarity,
                    cross_variation=cross_variation,
                    includes_main=includes_main,
                )
                if same_absolute_path:
                    return ("redundant_surface", "LOW", "low", evidence)
            if strong_alignment and content_similarity is not None and content_similarity >= 0.95 and exact_two_way:
                return ("redundant_surface", "MEDIUM", "medium", evidence)
            return ("manual_review", "MEDIUM", "low", evidence)
        risk = "low" if "companion" in roles else "medium"
        return ("manual_review", "MEDIUM", risk, evidence)

    lines = int(cluster.get("lines", 0) or 0)
    if low_signal:
        return ("redundant_surface", "LOW", "low", evidence)

    content_similarity, same_absolute_path = _content_similarity(atlas, instances)
    evidence["content_similarity"] = content_similarity
    evidence["same_absolute_path"] = same_absolute_path
    evidence["duplicate_subtype"] = _duplicate_subtype(
        content_similarity,
        cross_variation=cross_variation,
        includes_main=includes_main,
    )
    if same_absolute_path:
        return ("redundant_surface", "LOW", "low", evidence)

    if (
        content_similarity is not None
        and content_similarity < 0.6
        and lines < 20
        and not alignment_flags.get("same_stem", False)
    ):
        return ("redundant_surface", "LOW", "low", evidence)

    if content_similarity is None or content_similarity < 0.75:
        return ("manual_review", "MEDIUM", "medium", evidence)

    if exact_two_way and strong_alignment:
        if low_signal:
            return ("redundant_surface", "LOW", "low", evidence)
        if content_similarity >= 0.98 and alignment_score >= 0.9 and lines >= 80:
            risk = "critical" if lines >= 80 else "high"
        elif content_similarity >= 0.95:
            risk = "medium"
        else:
            return ("manual_review", "MEDIUM", "medium", evidence)
        return ("duplicate_live", "HIGH", risk, evidence)

    if strong_alignment and len(projects) == 1 and lines >= 20 and content_similarity >= 0.95:
        return ("duplicate_live", "MEDIUM", "medium", evidence)

    return ("manual_review", "MEDIUM", "medium", evidence)


def _build_broken_live_findings() -> list[dict]:
    findings: list[dict] = []
    for payload in load_oracle_reports():
        status = str(payload.get("status") or "").upper()
        if status not in {"WARN", "FAIL", "REHYDRATE_REQUIRED"}:
            continue
        project = str(payload.get("project") or "UNKNOWN")
        display = project_display_name(project)
        for issue in payload.get("broken_imports", []) or []:
            if not isinstance(issue, dict):
                continue
            classification = str(issue.get("classification") or "")
            severity = str(issue.get("severity") or "medium").lower()
            if classification == "sanctuary_snapshot_gap" or status == "REHYDRATE_REQUIRED":
                finding_class = "manual_review"
                risk = "low" if status == "REHYDRATE_REQUIRED" else "medium"
                confidence = "LOW" if status == "REHYDRATE_REQUIRED" else "MEDIUM"
                recommended_action = "Refresh sanctuary hydration first; only treat this as broken code if the issue survives a fresh snapshot."
            else:
                finding_class = "broken_live"
                risk = "critical" if severity == "high" and status == "FAIL" else ("high" if severity == "high" else "medium")
                confidence = "HIGH" if classification in HIGH_CONFIDENCE_ORACLE_CLASSES else "MEDIUM"
                recommended_action = "Repair the import contract or refresh sanctuary truth before trusting automated merges."
            findings.append(
                {
                    "project": project,
                    "display_name": display,
                    "file": _normalize_path(issue.get("file") or ""),
                    "classification": finding_class,
                    "confidence": confidence,
                    "risk_tier": risk,
                    "why_not_dead": "Oracle reached this file through sanctuary validation, so the surface is still reachable.",
                    "why_problematic": str(issue.get("reason") or "Oracle detected a live structural problem."),
                    "recommended_action": recommended_action,
                    "related_files": [str(issue.get("import") or "")] if issue.get("import") else [],
                    "evidence": {
                        "oracle_status": status,
                        "classification": classification,
                        "severity": severity,
                        "dependency_context": issue.get("dependency_context"),
                        "resolution_scope": issue.get("resolution_scope"),
                        "resolution_detail": issue.get("resolution_detail"),
                        "import": issue.get("import"),
                    },
                }
            )
    return findings


def _build_duplicate_live_findings(atlas: dict, dead_payload: dict, clone_payload: dict) -> list[dict]:
    dead_scoped_files, dead_scoped_symbols = _build_dead_index(dead_payload)
    findings: list[dict] = []
    for cluster in clone_payload.get("clusters", []) if isinstance(clone_payload, dict) else []:
        if not isinstance(cluster, dict):
            continue
        parsed_instances = []
        for instance in cluster.get("instances", []) or []:
            parsed = _parse_clone_instance(instance.get("id"))
            if not parsed:
                continue
            scoped_file = f"{parsed['project']}::{parsed['file']}"
            scoped_symbol = f"{scoped_file}::{parsed['symbol']}"
            if scoped_file in dead_scoped_files or scoped_symbol in dead_scoped_symbols:
                continue
            parsed_instances.append(parsed)

        if len(parsed_instances) < 2:
            continue

        classification, confidence, risk_tier, evidence = _classify_duplicate_cluster(atlas, cluster, parsed_instances)
        primary = parsed_instances[0]
        findings.append(
            {
                "project": primary["project"],
                "display_name": project_display_name(primary["project"]),
                "file": primary["file"],
                "classification": classification,
                "confidence": confidence,
                "risk_tier": risk_tier,
                "why_not_dead": "Semantic clone instances remain reachable because they were excluded from dead-code findings.",
                "why_problematic": (
                    "Multiple live files appear to provide the same surface and may split maintenance or behavior truth."
                    if classification != "redundant_surface"
                    else "Parallel surfaces across MAIN/variations or sibling variations may indicate a migration candidate rather than an in-place conflict."
                ),
                "recommended_action": (
                    "Review the related files and decide whether to consolidate, differentiate, or explicitly keep both."
                    if classification != "redundant_surface"
                    else "Treat this as a migration hint first: compare the related files, then decide whether MAIN should absorb one variation surface or keep them intentionally separate."
                ),
                "related_files": [f"{item['project']}::{item['file']}" for item in parsed_instances[1:]],
                "evidence": evidence,
            }
        )
    return findings


def _summary(findings: list[dict]) -> dict:
    class_counter = Counter(item["classification"] for item in findings)
    risk_counter = Counter(item["risk_tier"] for item in findings)
    duplicate_subtypes = Counter(
        (item.get("evidence") or {}).get("duplicate_subtype")
        for item in findings
        if item.get("classification") == "duplicate_live" and isinstance(item.get("evidence"), dict)
    )
    return {
        "total": len(findings),
        "broken_live": class_counter.get("broken_live", 0),
        "duplicate_live": class_counter.get("duplicate_live", 0),
        "redundant_surface": class_counter.get("redundant_surface", 0),
        "manual_review": class_counter.get("manual_review", 0),
        "duplicate_subtypes": {key: value for key, value in duplicate_subtypes.items() if key},
        "risk_tiers": {
            "critical": risk_counter.get("critical", 0),
            "high": risk_counter.get("high", 0),
            "medium": risk_counter.get("medium", 0),
            "low": risk_counter.get("low", 0),
        },
    }


def _dedupe_findings(findings: list[dict]) -> list[dict]:
    risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    confidence_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    deduped: dict[tuple, dict] = {}
    for item in findings:
        related = sorted(str(value) for value in (item.get("related_files", []) or []))
        identity = [f"{item.get('project')}::{item.get('file')}"]
        identity.extend(related)
        key = (item.get("classification"), tuple(sorted(identity)))
        current = deduped.get(key)
        if current is None:
            clone = dict(item)
            clone["related_files"] = related
            evidence = dict(clone.get("evidence", {})) if isinstance(clone.get("evidence"), dict) else {}
            evidence["merged_cluster_ids"] = [evidence.get("cluster_id")] if evidence.get("cluster_id") is not None else []
            clone["evidence"] = evidence
            deduped[key] = clone
            continue

        current_evidence = current.get("evidence", {}) if isinstance(current.get("evidence"), dict) else {}
        new_evidence = item.get("evidence", {}) if isinstance(item.get("evidence"), dict) else {}
        merged_ids = set(current_evidence.get("merged_cluster_ids", []) or [])
        if new_evidence.get("cluster_id") is not None:
            merged_ids.add(new_evidence.get("cluster_id"))

        current_rank = (
            risk_order.get(str(current.get("risk_tier")), 9),
            confidence_order.get(str(current.get("confidence")), 9),
            -int(current_evidence.get("lines", 0) or 0),
        )
        new_rank = (
            risk_order.get(str(item.get("risk_tier")), 9),
            confidence_order.get(str(item.get("confidence")), 9),
            -int(new_evidence.get("lines", 0) or 0),
        )
        if new_rank < current_rank:
            replacement = dict(item)
            replacement["related_files"] = related
            evidence = dict(new_evidence)
            evidence["merged_cluster_ids"] = sorted(merged_ids)
            replacement["evidence"] = evidence
            deduped[key] = replacement
        else:
            current_evidence["merged_cluster_ids"] = sorted(merged_ids)
            current["evidence"] = current_evidence

    return list(deduped.values())


def _write_markdown(payload: dict) -> None:
    summary = payload.get("summary", {})
    findings = payload.get("findings", [])
    lines = [
        "# Live Surface Findings",
        "",
        f"- Total findings: `{summary.get('total', 0)}`",
        f"- Broken live: `{summary.get('broken_live', 0)}`",
        f"- Duplicate live: `{summary.get('duplicate_live', 0)}`",
        f"- Manual review: `{summary.get('manual_review', 0)}`",
        f"- Duplicate subtypes: `{summary.get('duplicate_subtypes', {})}`",
        f"- Risk tiers: `critical={summary.get('risk_tiers', {}).get('critical', 0)}`, `high={summary.get('risk_tiers', {}).get('high', 0)}`, `medium={summary.get('risk_tiers', {}).get('medium', 0)}`, `low={summary.get('risk_tiers', {}).get('low', 0)}`",
        "",
        "| Risk | Class | Project | File | Related | Why problematic |",
        "|---|---|---|---|---:|---|",
    ]
    for item in findings:
        lines.append(
            f"| `{item.get('risk_tier')}` | `{item.get('classification')}` | `{item.get('project')}` | "
            f"`{item.get('file')}` | {len(item.get('related_files', []))} | {item.get('why_problematic')} |"
        )
    save_text_atomic(REPORT_OUTPUT, "\n".join(lines) + "\n")


def _build_priority_pack(findings: list[dict]) -> dict:
    priority_items = [
        item for item in findings
        if item.get("classification") == "duplicate_live" and item.get("risk_tier") in {"critical", "high", "medium"}
    ]
    risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    priority_items.sort(
        key=lambda item: (
            risk_order.get(str(item.get("risk_tier")), 9),
            -float((item.get("evidence", {}) if isinstance(item.get("evidence"), dict) else {}).get("responsibility_alignment_score", 0.0) or 0.0),
            -int((item.get("evidence", {}) if isinstance(item.get("evidence"), dict) else {}).get("lines", 0) or 0),
            str(item.get("project")),
            str(item.get("file")),
        )
    )
    top = priority_items[:12]
    return {
        "summary": {
            "total_candidates": len(priority_items),
            "selected": len(top),
            "risk_tiers": dict(Counter(item.get("risk_tier") for item in top)),
        },
        "items": top,
    }


def _write_priority_pack_md(payload: dict) -> None:
    items = payload.get("items", [])
    lines = [
        "# Live Surface Priority Review Pack",
        "",
        f"- Selected candidates: `{payload.get('summary', {}).get('selected', 0)}`",
        f"- Total duplicate-live candidates in priority tiers: `{payload.get('summary', {}).get('total_candidates', 0)}`",
        "",
        "| Risk | Project | File | Related | Alignment | Lines |",
        "|---|---|---|---:|---:|---:|",
    ]
    for item in items:
        evidence = item.get("evidence", {}) if isinstance(item.get("evidence"), dict) else {}
        lines.append(
            f"| `{item.get('risk_tier')}` | `{item.get('project')}` | `{item.get('file')}` | "
            f"{len(item.get('related_files', []))} | {evidence.get('responsibility_alignment_score', 0)} | {evidence.get('lines', 0)} |"
        )
    save_text_atomic(PRIORITY_REPORT_OUTPUT, "\n".join(lines) + "\n")


def run_live_surface_analyzer() -> dict:
    logger.info("Running live surface analyzer...")
    atlas = load_atlas_data()
    dead_payload = load_json_file(RAW_DIR / "dead_code.json", {})
    clone_payload = load_json_file(RAW_DIR / "clone_detector.json", {})

    findings = []
    findings.extend(_build_broken_live_findings())
    findings.extend(_build_duplicate_live_findings(atlas, dead_payload, clone_payload))
    findings = _dedupe_findings(findings)
    findings.sort(
        key=lambda item: (
            {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(item.get("risk_tier"), 9),
            item.get("classification", ""),
            item.get("project", ""),
            item.get("file", ""),
        )
    )

    payload = {
        "summary": _summary(findings),
        "findings": findings,
    }
    ensure_valid_payload("live_surface_findings", payload)
    save_json_atomic(RAW_OUTPUT, payload)
    _write_markdown(payload)

    priority_pack = _build_priority_pack(findings)
    ensure_valid_payload("live_surface_priority_pack", priority_pack)
    save_json_atomic(PRIORITY_RAW_OUTPUT, priority_pack)
    write_current_atlas_lineage(
        artifact_id="live_surface_priority_pack",
        producer="tools.engines.live_surface_analyzer",
        artifact_payload=priority_pack,
        atlas=atlas,
        dependency_payloads={"dead_code": dead_payload, "clone_detector": clone_payload},
    )
    _write_priority_pack_md(priority_pack)

    logger.info(
        "Live surface analyzer completed: total=%s duplicate_live=%s broken_live=%s manual_review=%s",
        payload["summary"]["total"],
        payload["summary"]["duplicate_live"],
        payload["summary"]["broken_live"],
        payload["summary"]["manual_review"],
    )
    return payload


if __name__ == "__main__":
    run_live_surface_analyzer()
