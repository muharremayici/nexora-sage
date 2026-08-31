from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.ssot_optimization_policy import (
    ssot_dedicated_sqlite_first_validators,
    ssot_dedicated_validator_summaries,
    ssot_default_candidate_hint,
    ssot_default_priorities,
    ssot_large_artifact_min_bytes,
    ssot_priority_hints,
    ssot_profile_fields,
    ssot_profile_gated_heavy_artifacts,
    ssot_runtime_reference_roots,
    ssot_v1_critical_artifacts,
)


OUTPUT_JSON = RAW_DIR / "ssot_optimization_plan.json"
OUTPUT_MD = REPORTS_DIR / "ssot_optimization_plan.md"
PROFILE = {field: 0 for field in ssot_profile_fields()}


def _log(message: str) -> None:
    print(f"[SSOT_OPTIMIZATION] {message}", flush=True)


def _increment_profile(field: str) -> None:
    PROFILE[field] = int(PROFILE.get(field, 0) or 0) + 1


def _load_boundary_inventory() -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / "engine_source_content_boundary_inventory.json", {})
    return payload if isinstance(payload, dict) else {}


def _load_atlas_access() -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / "atlas_sqlite_first_access_validation.json", {})
    return payload if isinstance(payload, dict) else {}


def _load_genome_access() -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / "genome_sqlite_first_access_validation.json", {})
    return payload if isinstance(payload, dict) else {}


def _load_fractal_access() -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / "fractal_sqlite_first_access_validation.json", {})
    return payload if isinstance(payload, dict) else {}


def _load_large_artifact_access() -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / "large_artifact_sqlite_first_access_validation.json", {})
    return payload if isinstance(payload, dict) else {}


def _candidate_priority(item: dict[str, Any]) -> dict[str, str]:
    file_name = str(item.get("file") or "")
    priority_hints = ssot_priority_hints()
    if file_name in priority_hints:
        return priority_hints[file_name]
    boundary_class = str(((item.get("declared_boundary") or {}).get("class")) or "")
    priority = ssot_default_priorities().get(boundary_class, "P2")
    default_hint = ssot_default_candidate_hint()
    return {
        "priority": priority,
        "why": default_hint.get("why", "Declared SQLite snapshot candidate."),
        "recommended_action": default_hint.get("recommended_action", "Adopt the shared source snapshot helper."),
    }


def _atlas_reference_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in payload.get("undeclared_reference_candidates", []) or []:
        file_name = str(item.get("file") or "")
        text = str(item.get("text") or "")
        if file_name.startswith("tools/validate_"):
            continue
        if file_name == "tools/generate_ssot_optimization_plan.py":
            continue
        if "source_artifacts" in text or text.startswith('"output/.raw/'):
            actions.append(
                {
                    "priority": "P3",
                    "file": file_name,
                    "line": item.get("line"),
                    "kind": "documentation_or_provenance_reference",
                    "recommended_action": "Keep as provenance text unless this file also loads the artifact directly.",
                }
            )
        elif "atlas_path" in text or "atlas.json not found" in text:
            actions.append(
                {
                    "priority": "P1",
                    "file": file_name,
                    "line": item.get("line"),
                    "kind": "legacy_path_reference",
                    "recommended_action": "Replace legacy atlas_path checks/messages with central load_atlas_data semantics where the file consumes Atlas.",
                }
            )
    return actions


def _genome_reference_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in payload.get("undeclared_reference_candidates", []) or []:
        file_name = str(item.get("file") or "")
        text = str(item.get("text") or "")
        if file_name.startswith("tools/validate_"):
            continue
        if file_name == "tools/generate_ssot_optimization_plan.py":
            continue
        if "source_artifacts" in text or text.startswith('"output/.raw/'):
            actions.append(
                {
                    "priority": "P3",
                    "file": file_name,
                    "line": item.get("line"),
                    "kind": "documentation_or_provenance_reference",
                    "recommended_action": "Keep as provenance text unless this file also loads the artifact directly.",
                }
            )
        elif "genome.json not found" in text:
            actions.append(
                {
                    "priority": "P1",
                    "file": file_name,
                    "line": item.get("line"),
                    "kind": "legacy_path_reference",
                    "recommended_action": "Replace legacy genome path checks/messages with central load_genome_data semantics where the file consumes Genome.",
                }
            )
    return actions


def _fractal_reference_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in payload.get("undeclared_reference_candidates", []) or []:
        file_name = str(item.get("file") or "")
        text = str(item.get("text") or "")
        if file_name.startswith("tools/validate_"):
            continue
        if file_name == "tools/generate_ssot_optimization_plan.py":
            continue
        if "source_artifacts" in text or text.startswith('"output/.raw/'):
            actions.append(
                {
                    "priority": "P3",
                    "file": file_name,
                    "line": item.get("line"),
                    "kind": "documentation_or_provenance_reference",
                    "recommended_action": "Keep as provenance text unless this file also loads the artifact directly.",
                }
            )
        elif "fractal_map.json" in text:
            actions.append(
                {
                    "priority": "P1",
                    "file": file_name,
                    "line": item.get("line"),
                    "kind": "legacy_path_reference",
                    "recommended_action": "Replace legacy fractal_map shadow checks with SQLite-first proxy loading.",
                }
            )
    return actions


def build_plan() -> dict[str, Any]:
    started = time.perf_counter()
    PROFILE["artifact_reference_scans"] = 0
    PROFILE["files_scanned"] = 0
    profile_timings: dict[str, float] = {}

    def mark_timing(name: str, phase_started: float) -> None:
        profile_timings[name] = round((time.perf_counter() - phase_started) * 1000, 2)

    _log("START build_plan")
    phase_started = time.perf_counter()
    boundary = _load_boundary_inventory()
    atlas = _load_atlas_access()
    genome = _load_genome_access()
    fractal = _load_fractal_access()
    mark_timing("load_inputs_ms", phase_started)

    phase_started = time.perf_counter()
    source_candidates: list[dict[str, Any]] = []
    for item in boundary.get("sqlite_ready_candidates", []) or []:
        hint = _candidate_priority(item)
        source_candidates.append(
            {
                "priority": hint["priority"],
                "file": item.get("file"),
                "boundary_class": ((item.get("declared_boundary") or {}).get("class")),
                "source_reads": len(item.get("source_reads") or []),
                "artifact_loads": len(item.get("artifact_loads") or []),
                "why": hint["why"],
                "recommended_action": hint["recommended_action"],
            }
        )
    mark_timing("source_candidate_classification_ms", phase_started)

    phase_started = time.perf_counter()
    source_consumers: list[dict[str, Any]] = []
    for item in boundary.get("sqlite_source_snapshot_consumers", []) or []:
        source_consumers.append(
            {
                "file": item.get("file"),
                "boundary_class": ((item.get("declared_boundary") or {}).get("class")) or item.get("boundary_class"),
                "source_reads": len(item.get("source_reads") or []),
                "artifact_loads": len(item.get("artifact_loads") or []),
                "status": "sqlite_source_snapshot_consumer",
                "evidence": "Uses tools.core.source_snapshot_reader.load_source_text before live source fallback.",
            }
        )
    mark_timing("source_consumer_classification_ms", phase_started)

    phase_started = time.perf_counter()
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    source_candidates.sort(key=lambda row: (priority_order.get(str(row.get("priority")), 9), str(row.get("file") or "")))
    source_consumers.sort(key=lambda row: str(row.get("file") or ""))
    artifact_actions = _atlas_reference_actions(atlas) + _genome_reference_actions(genome) + _fractal_reference_actions(fractal)
    artifact_actions.sort(key=lambda row: (priority_order.get(str(row.get("priority")), 9), str(row.get("file") or "")))
    mark_timing("artifact_reference_action_classification_ms", phase_started)

    phase_started = time.perf_counter()
    large_artifacts = _large_managed_artifacts()
    mark_timing("large_artifact_scan_ms", phase_started)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    payload = {
        "meta": {
            "kind": "ssot_optimization_plan",
            "version": "v1",
            "source_artifacts": [
                "output/.raw/engine_source_content_boundary_inventory.json",
                "output/.raw/atlas_sqlite_first_access_validation.json",
                "output/.raw/genome_sqlite_first_access_validation.json",
                "output/.raw/fractal_sqlite_first_access_validation.json",
            ],
        },
        "summary": {
            "status": "PASS",
            "sqlite_snapshot_candidates": len(source_candidates),
            "sqlite_snapshot_consumers": len(source_consumers),
            "p0_candidates": sum(1 for item in source_candidates if item.get("priority") == "P0"),
            "artifact_reference_actions": len(artifact_actions),
            "fractal_sqlite_first_status": (fractal.get("summary") or {}).get("status"),
            "large_shadow_json_artifacts": len(large_artifacts),
            "elapsed_ms": elapsed_ms,
            "profile_timings": profile_timings,
            "artifact_reference_scans": PROFILE["artifact_reference_scans"],
            "runtime_files_scanned": PROFILE["files_scanned"],
            "principle": "SQLite is the primary managed artifact store; canonical source ingestion reads live files, while downstream source analyzers should prefer hash-bearing SQLite source snapshots.",
        },
        "source_snapshot_consumers": source_consumers,
        "source_snapshot_migration_candidates": source_candidates,
        "artifact_reference_actions": artifact_actions,
        "large_shadow_json_artifacts": large_artifacts,
        "claim_boundary": {
            "known": "Atlas, Genome and Fractal managed artifacts are SQLite-first validated; P0 source-content analyzers now consume SQLite source_snapshots before live fallback.",
            "unknown": "Not every downstream source-content analyzer has been migrated to source_snapshots.",
            "next_gate": "Migrate P1 candidates next, then re-run performance and source-boundary validators.",
        },
    }
    return payload


def _large_managed_artifacts() -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    access_payload = _load_large_artifact_access()
    access_artifacts = {
        str(item.get("artifact") or ""): item
        for item in (access_payload.get("artifacts") or [])
        if isinstance(item, dict) and str(item.get("artifact") or "")
    } if isinstance(access_payload, dict) else {}
    candidates = sorted(RAW_DIR.glob("*.json"), key=lambda item: item.stat().st_size if item.exists() else 0, reverse=True)
    large_candidates = []
    for path in candidates:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < ssot_large_artifact_min_bytes():
            continue
        large_candidates.append((path, size))
    _log(f"SCAN large_artifacts={len(large_candidates)}")
    for index, (path, size) in enumerate(large_candidates, start=1):
        name = path.name
        _log(f"SCAN artifact={index}/{len(large_candidates)} name={name} size_mb={round(size / 1024 / 1024, 2)}")
        access_row = access_artifacts.get(name)
        if access_row:
            references = _references_from_large_artifact_access(access_row)
            _increment_profile("artifact_reference_scans")
            scan_strategy = "large_artifact_sqlite_first_access_validation"
        elif name in ssot_dedicated_validator_summaries():
            references = _references_from_dedicated_validator(name)
            scan_strategy = ssot_dedicated_validator_summaries()[name]["strategy"]
        elif name == ".dead_code_project_cache.json":
            references = _dead_code_cache_policy_references()
            scan_strategy = "engine_cache_policy"
        else:
            references = _artifact_runtime_references(name)
            scan_strategy = "source_tree_fallback"
        dedicated_validator = ssot_dedicated_sqlite_first_validators().get(name)
        has_dedicated_validator = bool(dedicated_validator and (ROOT / dedicated_validator).exists())
        hot_references = [
            ref for ref in references
            if ref.get("kind") in {"load_json_file", "save_json_atomic", "raw_dir_reference"}
        ]
        if has_dedicated_validator:
            status = "sqlite_first_validator_present"
        elif hot_references:
            status = "generic_sqlite_proxy_with_hot_runtime_consumers"
        else:
            status = "generic_sqlite_proxy_or_shadow_provenance"
        artifacts.append(
            {
                "artifact": name,
                "size_mb": round(size / 1024 / 1024, 2),
                "status": status,
                "generated_output_policy": _generated_output_policy(name, status, hot_references),
                "runtime_reference_count": len(references),
                "hot_runtime_reference_count": len(hot_references),
                "dedicated_validator": dedicated_validator or "",
                "reference_scan_strategy": scan_strategy,
                "sample_runtime_references": references[:8],
                "recommended_action": _large_artifact_action(name, status, hot_references),
            }
        )
    return artifacts


def _references_from_dedicated_validator(artifact_name: str) -> list[dict[str, Any]]:
    policy = ssot_dedicated_validator_summaries().get(artifact_name, {})
    validator = ssot_dedicated_sqlite_first_validators().get(artifact_name, "")
    source_artifact = str(policy.get("artifact") or "")
    metric = str(policy.get("metric") or "")
    payload = load_json_file(RAW_DIR / source_artifact, {}) if source_artifact else {}
    summary = payload.get("summary") if isinstance(payload, dict) else {}
    status = (summary or {}).get("status", "UNKNOWN") if isinstance(summary, dict) else "UNKNOWN"
    metric_value = (summary or {}).get(metric, 0) if isinstance(summary, dict) else 0
    return [
        {
            "file": validator,
            "line": 1,
            "kind": "dedicated_sqlite_first_validator_summary",
            "text": f"{source_artifact} status={status} {metric}={metric_value}",
        }
    ]


def _dead_code_cache_policy_references() -> list[dict[str, Any]]:
    return [
        {
            "file": "tools/engines/dead_code_detector.py",
            "line": 28,
            "kind": "engine_cache_policy",
            "text": "DEAD_CODE_CACHE_FILENAME marks producer-owned cache state, not release truth.",
        },
        {
            "file": "tools/engines/dead_code_detector.py",
            "line": 277,
            "kind": "engine_cache_policy",
            "text": "_load_project_cache reads producer-controlled cache with version/fingerprint validation.",
        },
        {
            "file": "tools/engines/dead_code_detector.py",
            "line": 295,
            "kind": "engine_cache_policy",
            "text": "_persist_project_cache writes generated cache state excluded from clean distribution.",
        },
    ]


def _references_from_large_artifact_access(access_row: dict[str, Any]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for key in ("sample_proxy_loads", "sample_proxy_writes", "undeclared_reference_candidates"):
        for item in access_row.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "")
            if key == "sample_proxy_loads":
                kind = kind or "load_json_file"
            elif key == "sample_proxy_writes":
                kind = kind or "save_json_atomic"
            references.append(
                {
                    "file": item.get("file"),
                    "line": item.get("line"),
                    "kind": kind or "provenance_reference",
                    "text": str(item.get("text") or "")[:180],
                }
            )
    references.sort(key=lambda item: (str(item.get("file")), int(item.get("line") or 0)))
    return references


def _generated_output_policy(name: str, status: str, hot_references: list[dict[str, Any]]) -> dict[str, str]:
    if name == "keyword_scanner_gems.json":
        return {
            "class": "pipeline_suppressed_diagnostic_shadow",
            "default_profile": "diagnostic direct mode only",
            "trim_decision": "suppress_pipeline_generation",
            "reason": "Pipeline consumers use keyword_scanner_all; standalone gems shadow is retained only for direct diagnostic keyword scanner runs.",
        }
    if name == ".dead_code_project_cache.json":
        return {
            "class": "engine_cache",
            "default_profile": "producer-controlled cache",
            "trim_decision": "keep_cache_but_exclude_from_clean_distribution",
            "reason": "Dead Code uses this cache to avoid repeated project-wide recomputation; it is generated state, not a release claim artifact.",
        }
    if name in ssot_v1_critical_artifacts():
        return {
            "class": "v1_critical",
            "default_profile": "daily/full/release-deep as scheduled",
            "trim_decision": "keep",
            "reason": "Feeds core target-repository governance, agent-facing context, or release proof claims.",
        }
    if name.endswith("_full.json"):
        return {
            "class": "profile_gated_full_payload",
            "default_profile": "full/release-deep",
            "trim_decision": "keep_profile_gated",
            "reason": "Full-evidence companion artifact; should not be generated by narrow watchdog/live pulses unless explicitly requested.",
        }
    if name in ssot_profile_gated_heavy_artifacts():
        return {
            "class": "profile_gated_heavy",
            "default_profile": "full/release-deep or explicit step",
            "trim_decision": "keep_profile_gated",
            "reason": "Broad synthesis artifact with real downstream value, but too heavy for save-time/live agent loops.",
        }
    if not hot_references and status == "generic_sqlite_proxy_or_shadow_provenance":
        return {
            "class": "shadow_or_provenance_trim_candidate",
            "default_profile": "release-deep or explicit provenance/debug workflow",
            "trim_decision": "review_for_generation_suppression",
            "reason": "No hot runtime consumer found; keep until a human confirms it is not needed for evidence, debug, or compatibility.",
        }
    return {
        "class": "managed_artifact_monitor",
        "default_profile": "follow producing engine profile",
        "trim_decision": "monitor",
        "reason": "Managed raw artifact with runtime consumers; keep SQLite-first and revisit only with consumer evidence.",
    }


def _artifact_runtime_references(artifact_name: str) -> list[dict[str, Any]]:
    _increment_profile("artifact_reference_scans")
    references: list[dict[str, Any]] = []
    roots: list[Path] = []
    for root_name in ssot_runtime_reference_roots():
        path = ROOT / root_name
        if path.exists():
            roots.append(path)
    suffixes = {".py", ".cjs", ".js", ".ts", ".tsx", ".json"}
    for root in roots:
        files = [root] if root.is_file() else [item for item in root.rglob("*") if item.is_file() and item.suffix.lower() in suffixes]
        for file_path in files:
            _increment_profile("files_scanned")
            rel = file_path.relative_to(ROOT).as_posix()
            if rel.startswith(("tools/archive/", "tools/tests/")):
                continue
            try:
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, start=1):
                dynamic_match = _dynamic_artifact_reference_match(artifact_name, rel, line)
                if artifact_name not in line and not dynamic_match:
                    continue
                stripped = line.strip()
                if dynamic_match:
                    kind = "dynamic_artifact_reference"
                elif "load_json_file" in stripped:
                    kind = "load_json_file"
                elif "save_json_atomic" in stripped:
                    kind = "save_json_atomic"
                elif "RAW_DIR" in stripped:
                    kind = "raw_dir_reference"
                else:
                    kind = "provenance_reference"
                references.append(
                    {
                        "file": rel,
                        "line": line_no,
                        "kind": kind,
                        "text": stripped[:180],
                    }
                )
    references.sort(key=lambda item: (str(item.get("file")), int(item.get("line") or 0)))
    return references


def _dynamic_artifact_reference_match(artifact_name: str, rel_path: str, line: str) -> bool:
    if artifact_name == ".dead_code_project_cache.json":
        return (
            rel_path == "tools/engines/dead_code_detector.py"
            and (
                "DEAD_CODE_CACHE_FILENAME" in line
                or "_project_cache_path" in line
                or "_load_project_cache" in line
                or "_persist_project_cache" in line
            )
        )
    if artifact_name.startswith("keyword_scanner_") and rel_path == "tools/engines/keyword_scanner.py":
        mode = artifact_name.removeprefix("keyword_scanner_").removesuffix(".json")
        if "keyword_scanner_{mode}.json" in line:
            return mode in {"stats", "keywords", "gems"}
        if mode == "all" and "keyword_scanner_all.json" in line:
            return True
    return False


def _large_artifact_action(name: str, status: str, hot_references: list[dict[str, Any]]) -> str:
    if status == "sqlite_first_validator_present":
        return "Keep dedicated SQLite-first validator in release proof and monitor hot consumers."
    if status == "generic_sqlite_proxy_with_hot_runtime_consumers":
        hot_files = sorted({str(item.get("file")) for item in hot_references if item.get("file")})
        if name in {"landscape_map.json", "surgical_discovery.json"}:
            return (
                "Generic .raw proxy routes normal loads through SQLite, but this large artifact has hot runtime consumers "
                f"({', '.join(hot_files[:4])}). Add a dedicated SQLite-first validator if it remains agent-facing or release-critical."
            )
        return (
            "Generic .raw proxy routes normal loads through SQLite. Keep hot consumers on load_json_file/save_json_atomic and add a dedicated validator only if release-critical."
        )
    return "No hot runtime consumer found in SAGE code; keep as shadow/provenance unless future consumers make it release-critical."


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# SSOT Optimization Plan",
        "",
        f"- status: `{summary.get('status')}`",
        f"- sqlite snapshot candidates: `{summary.get('sqlite_snapshot_candidates')}`",
        f"- sqlite snapshot consumers: `{summary.get('sqlite_snapshot_consumers')}`",
        f"- P0 candidates: `{summary.get('p0_candidates')}`",
        f"- artifact reference actions: `{summary.get('artifact_reference_actions')}`",
        f"- large shadow JSON artifacts: `{summary.get('large_shadow_json_artifacts')}`",
        f"- elapsed_ms: `{summary.get('elapsed_ms')}`",
        f"- profile_timings: `{summary.get('profile_timings')}`",
        f"- runtime files scanned: `{summary.get('runtime_files_scanned')}`",
        "",
        "## Boundary",
        "",
        payload.get("claim_boundary", {}).get("known", ""),
        "",
        payload.get("claim_boundary", {}).get("unknown", ""),
        "",
        "## Source Snapshot Consumers",
        "",
    ]
    consumers = payload.get("source_snapshot_consumers", [])
    if consumers:
        lines.extend(["| Engine | Reads | Loads | Evidence |", "|---|---:|---:|---|"])
        for item in consumers:
            lines.append(
                f"| `{item.get('file')}` | {item.get('source_reads')} | "
                f"{item.get('artifact_loads')} | {item.get('evidence')} |"
            )
        lines.append("")
    else:
        lines.extend(["No downstream source analyzers have been migrated to source snapshots yet.", ""])
    lines.extend([
        "## Source Snapshot Migration Candidates",
        "",
        "| Priority | Engine | Reads | Loads | Action |",
        "|---|---|---:|---:|---|",
    ])
    for item in payload.get("source_snapshot_migration_candidates", []):
        lines.append(
            f"| `{item.get('priority')}` | `{item.get('file')}` | {item.get('source_reads')} | "
            f"{item.get('artifact_loads')} | {item.get('recommended_action')} |"
        )
    lines.extend(["", "## Artifact Reference Actions", "", "| Priority | File | Line | Kind | Action |", "|---|---|---:|---|---|"])
    for item in payload.get("artifact_reference_actions", []):
        lines.append(
            f"| `{item.get('priority')}` | `{item.get('file')}` | {item.get('line')} | "
            f"`{item.get('kind')}` | {item.get('recommended_action')} |"
        )
    lines.extend(["", "## Large Shadow JSON Artifacts", "", "| Artifact | Size MB | Status | Action |", "|---|---:|---|---|"])
    for item in payload.get("large_shadow_json_artifacts", []):
        policy = item.get("generated_output_policy", {}) if isinstance(item.get("generated_output_policy"), dict) else {}
        lines.append(
            f"| `{item.get('artifact')}` | {item.get('size_mb')} | `{item.get('status')}` | "
            f"{item.get('recommended_action')}<br/>policy: `{policy.get('class', 'unknown')}` / `{policy.get('trim_decision', 'monitor')}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = build_plan()
    save_json_atomic(OUTPUT_JSON, payload)
    save_text_atomic(OUTPUT_MD, render_report(payload))
    _log(
        "DONE "
        f"status={payload['summary'].get('status')} "
        f"elapsed_ms={payload['summary'].get('elapsed_ms')} "
        f"profile_timings={payload['summary'].get('profile_timings')} "
        f"runtime_files_scanned={payload['summary'].get('runtime_files_scanned')}"
    )
    print(
        "[SSOT_OPTIMIZATION_PROFILE] "
        f"elapsed_ms={payload['summary'].get('elapsed_ms')} "
        f"profile_timings={payload['summary'].get('profile_timings')} "
        f"artifact_reference_scans={payload['summary'].get('artifact_reference_scans')} "
        f"runtime_files_scanned={payload['summary'].get('runtime_files_scanned')}",
        flush=True,
    )
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
