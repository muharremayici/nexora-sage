#!/usr/bin/env python3
"""
Generate evidence pack for studio-level donor decisions.

Inputs:
  - _docs/fractal_architecture_map_v16_3.json
  - _docs/main_project_baseline_package_2026-03-07.md (optional reference)

Outputs:
  - output/reports/decision_evidence.md
  - output/.raw/decision_evidence.json
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from statistics import mean
from typing import Dict, Iterable, List

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.core.config import OUTPUT_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic, DOCTRINE
from tools.core.logger import logger
from tools.core.config import ROOT
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file, load_json_strict
from tools.core.fractal_io import load_fractal_map_data
from tools.core.path_engine import to_os_path, to_posix_path
from tools.core.projects_registry import (
    STUDIO_TO_MODULE,
    canonical_project_name,
    project_display_name,
    resolve_runtime_projects,
)
from tools.core.source_files import is_analysis_source_file
from tools.core.studio_resolver import PLATFORM_CORE_STUDIO, studio_for_main_relative_path
from tools.core.workspace_mode import get_workspace_mode, is_source_allowed_for_host_merge
from tools.engines.fractal_mapper import LOW_SIGNAL_NAME_STOPLIST
from tools.engines.merge_script_generator import _is_semantically_safe_copy, _verify_source_semantics
import os
INPUT_JSON = RAW_DIR / "fractal_map.json"
OUT_MD = REPORTS_DIR / "decision_evidence.md"
OUT_JSON = RAW_DIR / "decision_evidence.json"


DECISION_EVIDENCE_DOCTRINE = require_doctrine_mapping("decision_evidence_doctrine")
DEFAULT_STUDIO_ORDER = [
    PLATFORM_CORE_STUDIO if s == "platform" else s
    for s in DECISION_EVIDENCE_DOCTRINE.get("target_studios", ["platform"])
]
FOCUS_STUDIO = str(DECISION_EVIDENCE_DOCTRINE.get("focus_studio") or (DEFAULT_STUDIO_ORDER[0] if DEFAULT_STUDIO_ORDER else PLATFORM_CORE_STUDIO))
FALLBACK_CAPABILITY_TAG = str(DECISION_EVIDENCE_DOCTRINE.get("fallback_capability_tag") or "other")

REPORT_DATE = date.today().isoformat()


@lru_cache(maxsize=1)
def _runtime_projects():
    return resolve_runtime_projects(ROOT)


def _to_float(value) -> float:
    if value is None:
        return 0.0
    return float(value)


def _norm_tokens(name: str) -> set[str]:
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name or "")
    return {p.lower() for p in parts if p}


@lru_cache(maxsize=1)
def _genome_data():
    data = load_genome_data()
    return data if isinstance(data, dict) else {}


def _normalize_file_lookup(path: str) -> str:
    return str(path or "").replace("\\", "/").strip("/").lower()


def _signature_from_occurrence(occ: dict) -> set[str]:
    tokens = set()
    tokens.update(_norm_tokens(occ.get("name", "")))
    for item in occ.get("architectural_markers", []) or []:
        tokens.update(_norm_tokens(item))
    for item in occ.get("member_architectural_markers", []) or []:
        tokens.update(_norm_tokens(item))
    for item in occ.get("ui_dependencies", []) or []:
        tokens.update(_norm_tokens(item))
    for item in occ.get("member_ui_dependencies", []) or []:
        tokens.update(_norm_tokens(item))
    for item in occ.get("contract_edges", []) or []:
        tokens.update(_norm_tokens(item))
    for item in occ.get("structural_tags", []) or []:
        tokens.update(_norm_tokens(item))

    for item in occ.get("imported_contracts", []) or []:
        parts = re.split(r"->|/|\\|:|\.", str(item))
        for part in parts:
            tokens.update(_norm_tokens(part))
    for item in occ.get("member_imported_contracts", []) or []:
        parts = re.split(r"->|/|\\|:|\.", str(item))
        for part in parts:
            tokens.update(_norm_tokens(part))
    for item in occ.get("member_dependencies", []) or []:
        tokens.update(_norm_tokens(str(item)))
    for item in occ.get("member_dynamic_imports", []) or []:
        parts = re.split(r"->|/|\\|:|\.", str(item))
        for part in parts:
            tokens.update(_norm_tokens(part))
    for item in occ.get("member_side_effect_markers", []) or []:
        tokens.update(_norm_tokens(str(item)))
    for item in occ.get("member_side_effect_imports", []) or []:
        parts = re.split(r"->|/|\\|:|\.", str(item))
        for part in parts:
            tokens.update(_norm_tokens(part))
    for item in occ.get("member_side_effect_calls", []) or []:
        parts = re.split(r"->|/|\\|:|\.|\[|\]|\(|\)", str(item))
        for part in parts:
            tokens.update(_norm_tokens(part))
    return {token for token in tokens if token}




def _risk_counts(items: Iterable[dict]) -> Dict[str, int]:
    c = Counter((i.get("risk") or "UNKNOWN").upper() for i in items)
    return {"LOW": c.get("LOW", 0), "MEDIUM": c.get("MEDIUM", 0), "HIGH": c.get("HIGH", 0)}


def _capability_tag(name: str) -> str:
    n = (name or "").lower()
    evidence_doctrine = DECISION_EVIDENCE_DOCTRINE
    rules = evidence_doctrine.get("capability_tags", [])
    for item in rules:
        tag = item.get("tag")
        keys = item.get("keys", [])
        if any(k in n for k in keys):
            return tag
    return FALLBACK_CAPABILITY_TAG


def _flat_feature_leaf(path: str) -> bool:
    parts = [p for p in (path or "").replace("\\", "/").split("/") if p]
    try:
        idx = parts.index("features")
    except ValueError:
        return False
    tail = parts[idx + 1 :]
    if len(tail) != 1:
        return False
    leaf = tail[0]
    stem = leaf.rsplit(".", 1)[0]
    return stem.islower()


def _is_trusted_candidate(item: dict) -> bool:
    name = (item.get("name") or "").strip()
    lowered = name.lower()
    target_layer = item.get("target_layer") or ""
    target_path = item.get("target_path_suggestion") or ""
    confidence = _to_float(item.get("confidence"))
    risk = (item.get("risk") or "").upper()
    review_bucket = str(item.get("review_bucket") or "").strip().lower()
    review_reasons = item.get("review_reasons", [])
    source_project = item.get("source_project")
    source_path = item.get("source_path")
    workspace_mode = get_workspace_mode()
    host_projects = workspace_mode.get("host_projects", []) or ["MAIN"]
    target_host = str(host_projects[0] if host_projects else "MAIN")

    if not name:
        return False
    if lowered in LOW_SIGNAL_NAME_STOPLIST:
        return False
    if item.get("merge_readiness") != "auto_merge":
        return False
    if review_bucket and review_bucket != "auto_merge_ready":
        return False
    if isinstance(review_reasons, list) and review_reasons:
        return False
    source_allowed, _ = is_source_allowed_for_host_merge(source_project=source_project, target_project=target_host)
    if not source_allowed:
        return False
    if not is_analysis_source_file(source_path or ""):
        return False
    if not is_analysis_source_file(target_path or ""):
        return False
    if not _is_semantically_safe_copy(item):
        return False

    # Physical Semantic Architecture Gate
    source_root = _runtime_projects().get(source_project)
    if source_root and source_path:
        src_abs = os.path.join(str(source_root), to_os_path(source_path))
        is_valid, _ = _verify_source_semantics(src_abs, item)
        if not is_valid:
            return False

    if confidence < 0.65:
        return False
    if risk not in {"LOW", "MEDIUM"}:
        return False
    if len(name) <= 2:
        return False
    if name.islower() and not name.startswith("use"):
        return False
    if target_layer == "shared/hooks" and not name.startswith("use"):
        return False
    if target_layer == "features" and _flat_feature_leaf(target_path):
        return False
    return True


def _main_files_by_studio(project_main: dict) -> Dict[str, int]:
    counts = Counter()
    for f in project_main.get("files", []):
        p = f.get("path", "")
        studio = studio_for_main_relative_path(p)
        if studio:
            counts[studio] += 1
    return dict(counts)


def _resolve_studio_order(decisions: list[dict]) -> list[str]:
    doctrine_order = [studio for studio in STUDIO_TO_MODULE.keys() if isinstance(studio, str) and studio]
    discovered = sorted(
        {
            str(item.get("target_studio") or "").strip()
            for item in decisions
            if isinstance(item, dict) and str(item.get("target_studio") or "").strip()
        }
    )

    ordered: list[str] = []
    for candidate in [PLATFORM_CORE_STUDIO, *doctrine_order, *DEFAULT_STUDIO_ORDER, *discovered]:
        if not candidate or candidate in ordered:
            continue
        ordered.append(candidate)
    return ordered


def _studio_summary(data: dict) -> List[dict]:
    decisions = data.get("all_decisions", [])
    studio_order = _resolve_studio_order(decisions if isinstance(decisions, list) else [])
    projects = data.get("projects", {}) or {}
    workspace_mode = get_workspace_mode()
    host_candidates = (workspace_mode.get("host_projects", []) or []) + ["MAIN"]
    host_project_key = next((name for name in host_candidates if name in projects), None)
    if host_project_key is None and projects:
        host_project_key = sorted(projects.keys())[0]
    main_files = _main_files_by_studio(projects.get(host_project_key, {}))

    by_studio_all = defaultdict(list)
    by_studio_imp = defaultdict(list)
    for d in decisions:
        by_studio_all[d.get("target_studio", "unknown")].append(d)
        if d.get("action") != "keep_main":
            by_studio_imp[d.get("target_studio", "unknown")].append(d)

    rows = []
    for studio in studio_order:
        all_items = by_studio_all.get(studio, [])
        imp_items = by_studio_imp.get(studio, [])
        src_count = Counter(i.get("chosen_source") for i in imp_items)
        r = _risk_counts(imp_items)
        conf = mean([_to_float(i.get("confidence")) for i in imp_items]) if imp_items else 0.0
        delta = mean([_to_float(i.get("delta")) for i in imp_items]) if imp_items else 0.0
        rows.append(
            {
                "studio": studio,
                "main_files": main_files.get(studio, 0),
                "total_decisions": len(all_items),
                "keep_main": sum(1 for i in all_items if i.get("action") == "keep_main"),
                "imports": len(imp_items),
                "auto_merge_ready": sum(1 for i in imp_items if i.get("merge_readiness") == "auto_merge"),
                "manual_review": sum(1 for i in imp_items if i.get("merge_readiness") != "auto_merge"),
                "source_mix": dict(sorted(src_count.items(), key=lambda x: (-x[1], str(x[0])))),
                "avg_confidence": round(conf, 3),
                "avg_delta": round(delta, 2),
                "risk": r,
            }
        )
    return rows


def _focus_deep_dive(data: dict, focus_studio: str = FOCUS_STUDIO) -> dict:
    decisions = [
        d
        for d in data.get("all_decisions", [])
        if d.get("target_studio") == focus_studio and d.get("action") != "keep_main"
    ]
    by_source = defaultdict(list)
    by_layer = defaultdict(list)
    tags = Counter()
    for d in decisions:
        src = canonical_project_name(d.get("chosen_source", "UNKNOWN"))
        by_source[src].append(d)
        by_layer[d.get("target_layer", "unknown")].append(d)
        tags[(src, _capability_tag(d.get("name", "")))] += 1

    trusted = [d for d in decisions if _is_trusted_candidate(d)]
    review_only = [d for d in decisions if not _is_trusted_candidate(d)]

    top = sorted(trusted, key=lambda x: (_to_float(x.get("delta")), _to_float(x.get("confidence"))), reverse=True)[:40]
    top_view = [
        {
            "name": t.get("display_name") or t.get("name"),
            "source": t.get("chosen_source"),
            "layer": t.get("target_layer"),
            "delta": round(_to_float(t.get("delta")), 2),
            "confidence": round(_to_float(t.get("confidence")), 2),
            "risk": t.get("risk"),
            "target": t.get("target_path_suggestion"),
        }
        for t in top
    ]

    review_view = [
        {
            "name": t.get("display_name") or t.get("name"),
            "source": t.get("chosen_source"),
            "layer": t.get("target_layer"),
            "delta": round(_to_float(t.get("delta")), 2),
            "confidence": round(_to_float(t.get("confidence")), 2),
            "risk": t.get("risk"),
            "target": t.get("target_path_suggestion"),
            "merge_readiness": t.get("merge_readiness"),
            "review_bucket": t.get("review_bucket", "target_contract_weak"),
        }
        for t in sorted(review_only, key=lambda x: (_to_float(x.get("delta")), _to_float(x.get("confidence"))), reverse=True)[:20]
    ]

    src_stats = []
    for src, items in sorted(by_source.items(), key=lambda x: len(x[1]), reverse=True):
        r = _risk_counts(items)
        src_stats.append(
            {
                "source": src,
                "count": len(items),
                "avg_confidence": round(mean([_to_float(i.get("confidence")) for i in items]), 3),
                "avg_delta": round(mean([_to_float(i.get("delta")) for i in items]), 2),
                "risk": r,
            }
        )

    layer_stats = []
    for layer, items in sorted(by_layer.items(), key=lambda x: len(x[1]), reverse=True):
        layer_stats.append(
            {
                "layer": layer,
                "count": len(items),
                "avg_confidence": round(mean([_to_float(i.get("confidence")) for i in items]), 3),
                "avg_delta": round(mean([_to_float(i.get("delta")) for i in items]), 2),
            }
        )

    tag_rows = []
    for (src, tag), c in sorted(tags.items(), key=lambda x: x[1], reverse=True):
        tag_rows.append({"source": src, "tag": tag, "count": c})

    return {
        "source_stats": src_stats,
        "layer_stats": layer_stats,
        "capability_tags": tag_rows,
        "trusted_top_candidates": top_view,
        "review_queue_examples": review_view,
    }


def _project_summary(data: dict) -> dict:
    by_project = defaultdict(lambda: {
        "display_name": "",
        "trusted_candidates": 0,
        "review_candidates": 0,
        "auto_merge_ready": 0,
        "manual_review": 0,
        "studios": Counter(),
        "layers": Counter(),
        "risks": Counter(),
    })

    for item in data.get("all_decisions", []) or []:
        if not isinstance(item, dict):
            continue
        project = canonical_project_name(item.get("source_project") or item.get("chosen_source") or "UNKNOWN")
        bucket = by_project[project]
        bucket["display_name"] = project_display_name(project)
        bucket["studios"][item.get("target_studio") or "unknown"] += 1
        bucket["layers"][item.get("target_layer") or "unknown"] += 1
        bucket["risks"][(item.get("risk") or "UNKNOWN").upper()] += 1
        if _is_trusted_candidate(item):
            bucket["trusted_candidates"] += 1
        else:
            bucket["review_candidates"] += 1
        if item.get("merge_readiness") == "auto_merge":
            bucket["auto_merge_ready"] += 1
        else:
            bucket["manual_review"] += 1

    def _confidence_for_payload(payload: dict) -> dict:
        trusted = int(payload.get("trusted_candidates", 0) or 0)
        review = int(payload.get("review_candidates", 0) or 0)
        auto_ready = int(payload.get("auto_merge_ready", 0) or 0)
        manual_review = int(payload.get("manual_review", 0) or 0)
        risk_mix = payload.get("risks", Counter()) or Counter()
        total = trusted + review
        if total <= 0:
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
        trusted_ratio = trusted / total
        merge_total = max(1, auto_ready + manual_review)
        auto_ratio = auto_ready / merge_total
        risk_total = max(1, sum(int(v or 0) for v in risk_mix.values()))
        low_ratio = int(risk_mix.get("LOW", 0) or 0) / risk_total
        high_ratio = int(risk_mix.get("HIGH", 0) or 0) / risk_total
        score = (0.45 * trusted_ratio) + (0.30 * auto_ratio) + (0.25 * low_ratio) - (0.15 * high_ratio)
        score = max(0.0, min(1.0, round(score, 3)))
        if score >= 0.80:
            tier = "high"
        elif score >= 0.60:
            tier = "medium"
        else:
            tier = "low"
        return {
            "score": score,
            "tier": tier,
            "signals": {
                "decision_count": total,
                "trusted_ratio": round(trusted_ratio, 3),
                "auto_merge_ratio": round(auto_ratio, 3),
                "low_risk_ratio": round(low_ratio, 3),
                "high_risk_ratio": round(high_ratio, 3),
            },
        }

    rows = {
        project: {
            "display_name": payload["display_name"] or project_display_name(project),
            "summary": {
                "trusted_candidates": payload["trusted_candidates"],
                "review_candidates": payload["review_candidates"],
                "auto_merge_ready": payload["auto_merge_ready"],
                "manual_review": payload["manual_review"],
            },
            "confidence": _confidence_for_payload(payload),
            "top_studios": dict(payload["studios"].most_common(5)),
            "top_layers": dict(payload["layers"].most_common(5)),
            "risk_mix": {
                "LOW": payload["risks"].get("LOW", 0),
                "MEDIUM": payload["risks"].get("MEDIUM", 0),
                "HIGH": payload["risks"].get("HIGH", 0),
            },
        }
        for project, payload in sorted(by_project.items())
    }
    for project in sorted(_runtime_projects().keys()):
        rows.setdefault(
            project,
            {
                "display_name": project_display_name(project),
                "summary": {
                    "trusted_candidates": 0,
                    "review_candidates": 0,
                    "auto_merge_ready": 0,
                    "manual_review": 0,
                },
                "confidence": {
                    "score": 1.0,
                    "tier": "not_applicable",
                    "signals": {
                        "decision_count": 0,
                        "trusted_ratio": 1.0,
                        "auto_merge_ratio": 1.0,
                        "low_risk_ratio": 1.0,
                        "high_risk_ratio": 0.0,
                    },
                },
                "top_studios": {},
                "top_layers": {},
                "risk_mix": {"LOW": 0, "MEDIUM": 0, "HIGH": 0},
            },
        )
    return rows


def _workspace_confidence(by_project: dict) -> dict:
    weighted_score = 0.0
    total_weight = 0
    projects_with_decisions = 0
    for payload in (by_project or {}).values():
        if not isinstance(payload, dict):
            continue
        confidence = payload.get("confidence", {}) or {}
        summary = payload.get("summary", {}) or {}
        score = _to_float(confidence.get("score"))
        decision_count = int(summary.get("trusted_candidates", 0) or 0) + int(summary.get("review_candidates", 0) or 0)
        if decision_count > 0:
            projects_with_decisions += 1
            weighted_score += score * decision_count
            total_weight += decision_count
    if total_weight <= 0:
        return {
            "score": 1.0,
            "tier": "not_applicable",
            "signals": {"decision_count": 0, "projects_with_decisions": 0},
        }
    score = max(0.0, min(1.0, round(weighted_score / total_weight, 3)))
    if score >= 0.80:
        tier = "high"
    elif score >= 0.60:
        tier = "medium"
    else:
        tier = "low"
    return {
        "score": score,
        "tier": tier,
        "signals": {
            "decision_count": total_weight,
            "projects_with_decisions": projects_with_decisions,
        },
    }


def _get_structural_signature(data: dict, project: str, file_path: str) -> set[str]:
    normalized_project = canonical_project_name(project or "")
    search_path = _normalize_file_lookup(file_path)

    genome_tokens = set()
    for occs in _genome_data().values():
        for occ in occs:
            if canonical_project_name(occ.get("project", "")) != normalized_project:
                continue
            occ_path = _normalize_file_lookup(occ.get("file", ""))
            if occ_path == search_path or occ_path.endswith(search_path) or search_path.endswith(occ_path):
                genome_tokens.update(_signature_from_occurrence(occ))
    if genome_tokens:
        return genome_tokens

    proj_data = data.get("projects", {}).get(project, {})
    files = proj_data.get("files", [])
    
    # Fast norm
    search_path = _normalize_file_lookup(file_path)
    
    for f in files:
        if f.get("path", "").replace("\\", "/").lower() == search_path:
            syms = f.get("symbols", {})
            return set(
                syms.get("classes", []) +
                syms.get("functions", []) +
                syms.get("hooks", []) +
                syms.get("interfaces", []) +
                syms.get("types", [])
            )
    return set()


def _parity_probe_focus(data: dict, focus_studio: str = FOCUS_STUDIO) -> dict:
    items = [
        d
        for d in data.get("all_decisions", [])
        if d.get("target_studio") == focus_studio
        and d.get("action") in {"new_atom", "evo_upgrade"}
        and _is_trusted_candidate(d)
    ]

    by_target = defaultdict(list)
    for i in items:
        by_target[(i.get("target_path_suggestion") or "").lower()].append(i)

    collisions = []
    for _, vals in by_target.items():
        srcs = sorted({v.get("chosen_source") for v in vals})
        if len(srcs) < 2:
            continue
        token_sets = []
        for v in vals:
            sig = _get_structural_signature(data, v.get("source_project"), v.get("source_path", ""))
            # If a file has no symbols mapped in fractal AST, fallback to filename token
            if not sig:
                sig = _norm_tokens(v.get("name", ""))
            token_sets.append(sig)
            
        if not token_sets:
            continue
            
        base = token_sets[0]
        sims = []
        for ts in token_sets[1:]:
            union = len(base | ts) or 1
            sims.append(len(base & ts) / union)
            
        collisions.append(
            {
                "target": vals[0].get("target_path_suggestion"),
                "names": sorted({v.get("name") for v in vals}),
                "sources": srcs,
                "count": len(vals),
                "avg_confidence": round(mean([_to_float(v.get("confidence")) for v in vals]), 3),
                "avg_delta": round(mean([_to_float(v.get("delta")) for v in vals]), 2),
                "structural_similarity": round(mean(sims), 3) if sims else 1.0,
            }
        )
    collisions.sort(key=lambda x: (x["avg_delta"], x["avg_confidence"]), reverse=True)
    return {
        "screened_items": len(items),
        "multi_source_target_collisions": len(collisions),
        "top_collisions": collisions[:30],
    }


def _render_md(report: dict) -> str:
    lines: List[str] = []
    meta = report.get("meta", {}) or {}
    comparative_mode = bool(meta.get("comparative_mode", True))
    host_projects = meta.get("host_projects", []) or []
    variant_projects = meta.get("variant_projects", []) or []
    companion_projects = meta.get("companion_projects", []) or []
    lines.append("# Decision Evidence Pack v1")
    lines.append(f"Date: {REPORT_DATE}")
    lines.append("")
    lines.append("## Scope")
    lines.append("- Add measurable evidence for studio donor decisions.")
    lines.append("- Show which candidates are auto-merge ready versus manual-review only.")
    lines.append("")
    lines.append("## Workspace Scope")
    lines.append("| Signal | Value |")
    lines.append("|---|---|")
    lines.append(f"| Mode | `{meta.get('workspace_mode', 'unknown')}` |")
    lines.append(f"| Comparative Enabled | {'YES' if comparative_mode else 'NO'} |")
    workspace_confidence = meta.get("confidence", {}) or {}
    lines.append(
        f"| Evidence Confidence | `{workspace_confidence.get('score', 0.0)}` (`{workspace_confidence.get('tier', 'unknown')}`) |"
    )
    lines.append(
        f"| Host Projects | `{', '.join(f'{project_display_name(p)} [{p}]' for p in host_projects) or '-'}` |"
    )
    lines.append(
        f"| Variant Projects | `{', '.join(f'{project_display_name(p)} [{p}]' for p in variant_projects) or '-'}` |"
    )
    lines.append(
        f"| Companion Projects | `{', '.join(f'{project_display_name(p)} [{p}]' for p in companion_projects) or '-'}` |"
    )
    lines.append("")
    if not comparative_mode:
        lines.append("## Comparative Mode")
        lines.append("No variant donor projects were discovered in this workspace. Comparative donor evidence is not applicable in this run.")
        lines.append("Decision Evidence remains available as a compatibility artifact, but donor/import tables are intentionally empty.")
        lines.append("")
    lines.append("## Studio Benchmark")
    lines.append("| Studio | Main Files | Keep Main | Imports | Auto/Manual | Top Sources | Avg Conf | Avg Delta | Risk L/M/H |")
    lines.append("|---|---:|---:|---:|---|---|---:|---:|---|")
    for r in report["studio_summary"]:
        source_mix = r.get("source_mix", {}) or {}
        top_sources = ", ".join(f"{name}:{count}" for name, count in list(source_mix.items())[:3]) if source_mix else "-"
        lines.append(
            f"| {r['studio']} | {r['main_files']} | {r['keep_main']} | {r['imports']} | "
            f"{r['auto_merge_ready']}/{r['manual_review']} | "
            f"{top_sources} | {r['avg_confidence']:.3f} | "
            f"{r['avg_delta']:.2f} | {r['risk']['LOW']}/{r['risk']['MEDIUM']}/{r['risk']['HIGH']} |"
        )

    lines.append("")
    focus_studio = str(report.get("meta", {}).get("focus_studio") or FOCUS_STUDIO)
    focus_title = focus_studio.replace("_", " ").title()
    focus_deep_dive = report.get("focus_deep_dive") or {}
    focus_parity = report.get("parity_probe_focus") or {}

    lines.append(f"## {focus_title} Studio Evidence")
    lines.append("### Source Stats")
    lines.append("| Source | Count | Avg Conf | Avg Delta | Risk L/M/H |")
    lines.append("|---|---:|---:|---:|---|")
    for s in focus_deep_dive.get("source_stats", []):
        lines.append(
            f"| {s['source']} | {s['count']} | {s['avg_confidence']:.3f} | {s['avg_delta']:.2f} | "
            f"{s['risk']['LOW']}/{s['risk']['MEDIUM']}/{s['risk']['HIGH']} |"
        )

    lines.append("")
    lines.append("### Layer Profile")
    lines.append("| Layer | Count | Avg Conf | Avg Delta |")
    lines.append("|---|---:|---:|---:|")
    for l in focus_deep_dive.get("layer_stats", []):
        lines.append(f"| {l['layer']} | {l['count']} | {l['avg_confidence']:.3f} | {l['avg_delta']:.2f} |")

    lines.append("")
    lines.append(f"### Capability Tag Mix ({focus_title})")
    lines.append("| Source | Tag | Count |")
    lines.append("|---|---|---:|")
    for t in focus_deep_dive.get("capability_tags", [])[:25]:
        lines.append(f"| {t['source']} | {t['tag']} | {t['count']} |")

    lines.append("")
    lines.append(f"### Trusted Top {focus_title} Candidates")
    lines.append("> `Proposed Target Path` is the suggested host placement path, not proof that the file already exists in MAIN.")
    lines.append("")
    lines.append("| Name | Source | Layer | Delta | Conf | Risk | Proposed Target Path |")
    lines.append("|---|---|---|---:|---:|---|---|")
    for c in focus_deep_dive.get("trusted_top_candidates", [])[:20]:
        lines.append(
            f"| {c['name']} | {c['source']} | {c['layer']} | {c['delta']:.2f} | {c['confidence']:.2f} | "
            f"{c['risk']} | `{c['target']}` |"
        )

    lines.append("")
    lines.append("### Review Queue Examples (Not Evidence Authority)")
    lines.append("| Name | Source | Layer | Delta | Conf | Risk | Review Bucket | Readiness | Proposed Target Path |")
    lines.append("|---|---|---|---:|---:|---|---|---|---|")
    for c in focus_deep_dive.get("review_queue_examples", [])[:15]:
        lines.append(
            f"| {c['name']} | {c['source']} | {c['layer']} | {c['delta']:.2f} | {c['confidence']:.2f} | "
            f"{c['risk']} | {c.get('review_bucket', 'target_contract_weak')} | {c['merge_readiness']} | `{c['target']}` |"
        )

    lines.append("")
    lines.append(f"## Semantic Parity Probe ({focus_title})")
    parity = focus_parity
    lines.append(f"- Screened items: **{parity['screened_items']}**")
    lines.append(f"- Multi-source target collisions: **{parity['multi_source_target_collisions']}**")
    lines.append("")
    lines.append("| Proposed Target Path | Names | Sources | Avg Conf | Avg Delta | Structural Similarity (AST) |")
    lines.append("|---|---|---|---:|---:|---:|")
    for c in parity["top_collisions"][:20]:
        lines.append(
            f"| `{c['target']}` | {', '.join(c['names'])} | {', '.join(c['sources'])} | {c['avg_confidence']:.3f} | "
            f"{c['avg_delta']:.2f} | {c['structural_similarity']:.3f} |"
        )

    lines.append("")
    lines.append("## Verdict")
    auto_ready = report["meta"].get("auto_merge_ready", 0)
    manual_review = report["meta"].get("manual_review", 0)
    target_contract_weak = report["meta"].get("manual_review_target_contract_weak", 0)
    hitl_architecture_review = report["meta"].get("manual_review_hitl_architecture_review", 0)
    lines.append(f"1. Auto-merge ready candidates: **{auto_ready}**.")
    lines.append(f"2. Manual review queue: **{manual_review}**.")
    lines.append(f"3. Target-contract-weak review items: **{target_contract_weak}**.")
    lines.append(f"4. HITL architecture review items: **{hitl_architecture_review}**.")
    lines.append("5. Trusted evidence candidates are now separated from review-queue examples; this is safer than the previous model.")
    lines.append("")
    lines.append("## ADR Seeds")
    lines.append("- ADR-MR-001: Auto-merge candidates must be separated from manual-review candidates.")
    lines.append("- ADR-MR-002: Platform core remains review-heavy unless target confidence is strong.")
    lines.append("- ADR-MR-003: Merge scripts execute only the auto-merge-ready subset.")
    return "\n".join(lines) + "\n"


def main() -> None:
    data = load_fractal_map_data()
    if not isinstance(data, dict) or not data:
        logger.error("Cannot run decision_evidence: Fractal Map is unavailable from SQLite and shadow fallback. Run fractal_mapper first.")
        return
    project_count = len(data.get("projects", {}) or {})
    workspace_mode = get_workspace_mode()
    by_project = _project_summary(data)
    workspace_confidence = _workspace_confidence(by_project)
    focus_deep_dive = _focus_deep_dive(data, FOCUS_STUDIO)
    parity_probe_focus = _parity_probe_focus(data, FOCUS_STUDIO)
    report = {
        "studio_summary": _studio_summary(data),
        "focus_deep_dive": focus_deep_dive,
        "parity_probe_focus": parity_probe_focus,
        "by_project": by_project,
        "meta": {
            **(data.get("meta", {}) or {}),
            "focus_studio": FOCUS_STUDIO,
            "workspace_mode": workspace_mode.get("mode"),
            "comparative_mode": bool(workspace_mode.get("comparative_enabled")),
            "project_count": project_count,
            "host_projects": workspace_mode.get("host_projects", []),
            "variant_projects": workspace_mode.get("variant_projects", []),
            "companion_projects": workspace_mode.get("companion_projects", []),
            "confidence": workspace_confidence,
        },
    }
    save_json_atomic(OUT_JSON, report)
    save_text_atomic(OUT_MD, _render_md(report))
    logger.info(f"Wrote {to_posix_path(OUT_MD.relative_to(OUTPUT_DIR))}")
    logger.info(f"Wrote {to_posix_path(OUT_JSON.relative_to(OUTPUT_DIR))}")


if __name__ == "__main__":
    main()
