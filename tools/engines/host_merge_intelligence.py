import json
from collections import Counter, defaultdict
from pathlib import Path

from tools.core.config import RAW_DIR, REPORTS_DIR, SRC, ROOT, DOCTRINE, save_json_atomic, save_text_atomic
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.core.atlas_io import load_atlas_data
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.host_policy import classify_host_policy, load_host_policy, tag_host_file
from tools.core.json_io import load_json_file
from tools.core.fractal_io import load_fractal_map_data
from tools.core.logger import logger
from tools.core.path_engine import to_posix_path
from tools.core.projects_registry import STUDIO_TO_MODULE
from tools.core.fractal_policy import get_module_container
from tools.core.source_files import is_analysis_source_file
from tools.core.workspace_mode import get_workspace_mode
from tools.engines.decision_evidence import _is_trusted_candidate


def _to_float(value) -> float:
    if value is None:
        return 0.0
    return float(value)


def _confidence_from_merge_rows(fractal_rows, trusted_rows, merge_readiness) -> dict:
    decision_count = len(fractal_rows)
    if decision_count <= 0:
        return {
            "score": 1.0,
            "tier": "not_applicable",
            "signals": {
                "decision_count": 0,
                "trusted_ratio": 1.0,
                "auto_merge_ratio": 1.0,
                "low_risk_ratio": 1.0,
                "high_risk_ratio": 0.0,
            },
        }

    tuning = require_doctrine_mapping("intelligence_tuning")
    weights = tuning.get("merge_confidence_weights", {
        "trusted_ratio": 0.45,
        "auto_ratio": 0.30,
        "low_risk_ratio": 0.25,
        "high_risk_penalty": 0.15
    })
    tiers = tuning.get("confidence_tiers", {"high": 0.80, "medium": 0.60})

    trusted_ratio = len(trusted_rows) / decision_count
    auto_ready = int(merge_readiness.get("auto_merge", 0) or 0)
    auto_ratio = auto_ready / decision_count
    risk_counter = Counter((row.get("risk") or "UNKNOWN").upper() for row in fractal_rows if isinstance(row, dict))
    low_ratio = int(risk_counter.get("LOW", 0) or 0) / decision_count
    high_ratio = int(risk_counter.get("HIGH", 0) or 0) / decision_count
    
    # Formula using doctrine-driven weights
    score = (
        (weights.get("trusted_ratio", 0.45) * trusted_ratio) + 
        (weights.get("auto_ratio", 0.30) * auto_ratio) + 
        (weights.get("low_risk_ratio", 0.25) * low_ratio) - 
        (weights.get("high_risk_penalty", 0.15) * high_ratio)
    )
    score = max(0.0, min(1.0, round(score, 3)))
    
    if score >= tiers.get("high", 0.80):
        tier = "high"
    elif score >= tiers.get("medium", 0.60):
        tier = "medium"
    else:
        tier = "low"
        
    return {
        "score": score,
        "tier": tier,
        "signals": {
            "decision_count": decision_count,
            "trusted_ratio": round(trusted_ratio, 3),
            "auto_merge_ratio": round(auto_ratio, 3),
            "low_risk_ratio": round(low_ratio, 3),
            "high_risk_ratio": round(high_ratio, 3),
        },
    }


def _atlas_studio_files(studio_root: Path, atlas: dict) -> list[str]:
    files: list[str] = []
    try:
        resolved_studio_root = studio_root.resolve()
    except OSError:
        return files

    for project_payload in atlas.values() if isinstance(atlas, dict) else []:
        if not isinstance(project_payload, dict):
            continue
        project_meta = project_payload.get("project", {}) or {}
        project_root_text = project_meta.get("root")
        project_files = project_payload.get("files", {}) or {}
        if not isinstance(project_files, dict) or not project_root_text:
            continue
        try:
            project_root = Path(str(project_root_text)).resolve()
            studio_prefix = resolved_studio_root.relative_to(project_root).as_posix()
        except (OSError, ValueError):
            continue
        prefix = "" if studio_prefix == "." else studio_prefix.rstrip("/") + "/"
        for rel_path in project_files:
            rel_text = str(rel_path).replace("\\", "/")
            if prefix and not rel_text.startswith(prefix):
                continue
            studio_rel = rel_text[len(prefix):] if prefix else rel_text
            if not studio_rel or "/" not in rel_text and prefix:
                continue
            if not is_analysis_source_file(studio_rel):
                continue
            parts = Path(studio_rel).parts
            if "__tests__" in parts or studio_rel.endswith((".test.ts", ".spec.ts")):
                continue
            files.append(studio_rel)
    return sorted(set(files))


def _list_studio_files(studio_root: Path, atlas: dict):
    return _atlas_studio_files(studio_root, atlas)


def _summarize_donors(rows):
    donor_counter = Counter(row.get("chosen_source") for row in rows if row.get("chosen_source"))
    return [{"source": source, "count": count} for source, count in donor_counter.most_common(6)]


def _studio_payload(studio: str, module: str, studio_root: Path, policy, fractal_rows, atlas):
    files = _list_studio_files(studio_root, atlas)
    file_entries = []
    policy_counter = Counter()
    tag_counter = Counter()

    for rel in files:
        file_policy = classify_host_policy(rel, policy)
        tags = tag_host_file(rel, policy)
        policy_counter[file_policy] += 1
        tag_counter.update(tags)
        file_entries.append(
            {
                "path": rel,
                "policy": file_policy,
                "tags": tags,
            }
        )

    fractal_rows = sorted(fractal_rows, key=lambda row: row.get("delta", 0), reverse=True)
    trusted_rows = [row for row in fractal_rows if _is_trusted_candidate(row)]
    review_rows = [row for row in fractal_rows if not _is_trusted_candidate(row)]
    merge_readiness = Counter(row.get("merge_readiness") for row in fractal_rows)
    action_mix = Counter(row.get("action") for row in fractal_rows)
    merge_confidence = _confidence_from_merge_rows(fractal_rows, trusted_rows, merge_readiness)

    return {
        "studio": studio,
        "module": module,
        "root": studio_root.as_posix(),
        "host_summary": {
            "file_count": len(file_entries),
            "policy_counts": dict(policy_counter),
            "tag_counts": dict(tag_counter),
        },
        "merge_summary": {
            "decision_count": len(fractal_rows),
            "merge_readiness": dict(merge_readiness),
            "action_mix": dict(action_mix),
            "top_donors": _summarize_donors(fractal_rows),
            "confidence": merge_confidence,
        },
        "protected_files": [entry for entry in file_entries if entry["policy"] == "host_locked"][:120],
        "compose_preferred_files": [entry for entry in file_entries if entry["policy"] == "compose_preferred"][:160],
        "manual_only_files": [entry for entry in file_entries if entry["policy"] == "manual_only"][:160],
        "top_merge_candidates": [
            {
                "name": row.get("display_name") or row.get("name"),
                "source": row.get("chosen_source"),
                "action": row.get("action"),
                "target_layer": row.get("target_layer"),
                "delta": row.get("delta"),
                "confidence": row.get("confidence"),
                "risk": row.get("risk"),
                "merge_readiness": row.get("merge_readiness"),
                "target_path": row.get("target_path_suggestion"),
                "review_reasons": row.get("review_reasons", []),
            }
            for row in trusted_rows[:40]
        ],
        "top_review_candidates": [
            {
                "name": row.get("display_name") or row.get("name"),
                "source": row.get("chosen_source"),
                "action": row.get("action"),
                "target_layer": row.get("target_layer"),
                "delta": row.get("delta"),
                "confidence": row.get("confidence"),
                "risk": row.get("risk"),
                "merge_readiness": row.get("merge_readiness"),
                "review_bucket": row.get("review_bucket", "target_contract_weak"),
                "target_path": row.get("target_path_suggestion"),
                "review_reasons": row.get("review_reasons", []),
            }
            for row in review_rows[:20]
        ],
    }


def _resolve_studio_root(module_root: str, module: str) -> Path | None:
    module_root_norm = str(module_root or ".").strip("/")
    module_prefix = f"{module_root_norm}/" if module_root_norm and module_root_norm != "." else ""
    sub_path = f"{module_prefix}{module}" if module_prefix else module
    candidates = [
        SRC / sub_path,
        SRC / "lifecycle-modules" / module,
        ROOT / sub_path,
        ROOT / "src" / sub_path,
        ROOT / "src" / "lifecycle-modules" / module,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def run_host_merge_intelligence():
    logger.info("Building host-aware merge intelligence report...")
    policy = load_host_policy()
    fractal = load_fractal_map_data()
    atlas = load_atlas_data()
    decisions = fractal.get("all_decisions", [])
    workspace_mode = get_workspace_mode()

    studio_rows = defaultdict(list)
    for row in decisions:
        studio_rows[row.get("target_studio", "platform_core")].append(row)

    studios_payload = {}
    module_root = get_module_container()

    for studio, module in STUDIO_TO_MODULE.items():
        studio_root = _resolve_studio_root(module_root, module)
        if studio_root is None:
            continue
        studios_payload[studio] = _studio_payload(studio, module, studio_root, policy, studio_rows.get(studio, []), atlas)

    total_weight = 0
    weighted_score = 0.0
    studios_with_decisions = 0
    for studio_payload in studios_payload.values():
        merge_summary = studio_payload.get("merge_summary", {}) if isinstance(studio_payload, dict) else {}
        confidence = merge_summary.get("confidence", {}) if isinstance(merge_summary, dict) else {}
        score = _to_float(confidence.get("score"))
        decision_count = int(merge_summary.get("decision_count", 0) or 0)
        if decision_count > 0:
            studios_with_decisions += 1
            weighted_score += score * decision_count
            total_weight += decision_count
    tuning = require_doctrine_mapping("intelligence_tuning")
    tiers = tuning.get("confidence_tiers", {"high": 0.80, "medium": 0.60})

    if total_weight > 0:
        workspace_confidence_score = max(0.0, min(1.0, round(weighted_score / total_weight, 3)))
        workspace_confidence_tier = "high" if workspace_confidence_score >= tiers.get("high", 0.80) else ("medium" if workspace_confidence_score >= tiers.get("medium", 0.60) else "low")
    else:
        workspace_confidence_score = 1.0
        workspace_confidence_tier = "not_applicable"

    payload = {
        "meta": {"kind": "host_merge_intelligence", "version": "v1"},
        "workspace_mode": workspace_mode,
        "summary": {
            "studio_count": len(studios_payload),
            "decision_count": total_weight,
            "studios_with_decisions": studios_with_decisions,
            "confidence": {
                "score": workspace_confidence_score,
                "tier": workspace_confidence_tier,
                "signals": {
                    "decision_count": total_weight,
                    "studios_with_decisions": studios_with_decisions,
                },
            },
        },
        "policy": policy,
        "studios": studios_payload,
    }

    json_path = RAW_DIR / "host_merge_intelligence.json"
    save_json_atomic(json_path, payload)
    write_current_atlas_lineage(
        artifact_id="host_merge_intelligence",
        producer="tools.engines.host_merge_intelligence",
        artifact_payload=payload,
        atlas=atlas,
        dependency_payloads={"fractal_map": fractal},
    )

    lines = [
        "# Host Merge Intelligence",
        "",
        "Host-aware policy layer for safe merge planning.",
        "",
        "## Workspace Mode",
        f"- mode: `{workspace_mode.get('mode')}`",
        f"- project_count: `{workspace_mode.get('project_count')}`",
        f"- comparative_enabled: `{'yes' if workspace_mode.get('comparative_enabled') else 'no'}`",
        f"- merge confidence: `{workspace_confidence_score}` (`{workspace_confidence_tier}`)",
        "",
        "## Policy Summary",
        f"- host_locked patterns: `{len(policy['host_locked_patterns'])}`",
        f"- compose_preferred patterns: `{len(policy['compose_preferred_patterns'])}`",
        f"- manual_review patterns: `{len(policy['manual_review_patterns'])}`",
        "",
    ]

    if not workspace_mode.get("comparative_enabled"):
        lines += [
            "Comparative donor merge planning is not active in this workspace.",
            "This report should be read as a local host-boundary inventory and compose-safety surface.",
            "",
        ]

    for studio, data in studios_payload.items():
        summary = data["host_summary"]
        merge = data["merge_summary"]
        lines += [
            f"## Studio: {studio}",
            f"- module: `{data['module']}`",
            f"- host files: `{summary['file_count']}`",
            f"- policy counts: `{json.dumps(summary['policy_counts'], ensure_ascii=False)}`",
            f"- merge decisions: `{merge['decision_count']}`",
            f"- merge readiness: `{json.dumps(merge['merge_readiness'], ensure_ascii=False)}`",
            f"- merge confidence: `{merge.get('confidence', {}).get('score', 0.0)}` (`{merge.get('confidence', {}).get('tier', 'unknown')}`)",
            f"- top donors: `{json.dumps(merge['top_donors'], ensure_ascii=False)}`",
            "",
            "### Protected Files",
        ]
        for item in data["protected_files"][:20]:
            lines.append(f"- `{item['path']}`")
        lines += ["", "### Compose-Preferred Files"]
        for item in data["compose_preferred_files"][:20]:
            lines.append(f"- `{item['path']}`")
        lines += ["", "### Manual-Only Files"]
        for item in data["manual_only_files"][:20]:
            lines.append(f"- `{item['path']}`")
        lines += ["", "### Top Merge Candidates"]
        for item in data["top_merge_candidates"][:12]:
            lines.append(
                f"- `{item['name']}` <- {item['source']} | {item['action']} | "
                f"{item['merge_readiness']} | {item['risk']} | `{item['target_path']}`"
            )
        lines += ["", "### Review Queue Examples"]
        for item in data["top_review_candidates"][:8]:
            lines.append(
                f"- `{item['name']}` <- {item['source']} | {item['action']} | "
                f"{item['merge_readiness']} | {item.get('review_bucket', 'target_contract_weak')} | {item['risk']} | `{item['target_path']}`"
            )
        lines.append("")

    md_path = REPORTS_DIR / "host_merge_intelligence.md"
    save_text_atomic(md_path, "\n".join(lines))
    logger.info(f"Host merge intelligence written: {to_posix_path(md_path.relative_to(REPORTS_DIR.parent))}")


if __name__ == "__main__":
    run_host_merge_intelligence()
