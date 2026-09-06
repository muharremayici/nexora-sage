from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.config import (
    CODE_MAPS_DIR,
    CONFIG_DIR,
    DYNAMIC_CONFIG,
    RAW_DIR,
    REPORTS_DIR,
    ROOT,
    save_json_atomic,
    save_text_atomic,
)
from tools.core.atlas_io import load_atlas_data
from tools.core.honesty_telemetry import record_honesty_event
from tools.core.json_io import load_json_object_strict
from tools.core.projects_registry import resolve_runtime_projects


POLICY_PATH = CONFIG_DIR / "project_dna_profile_policy.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(CODE_MAPS_DIR.resolve()).as_posix()
    except Exception:
        try:
            return path.resolve().relative_to(ROOT.resolve()).as_posix()
        except Exception:
            return path.as_posix()


def _project_root(project_key: str) -> Path:
    variations = _as_dict(DYNAMIC_CONFIG.get("variations"))
    rel = str(variations.get(project_key, "") or "").replace("\\", "/").strip()
    if not rel or rel == ".":
        return ROOT
    return (ROOT / rel).resolve()


def _candidate_roots(project_key: str) -> list[Path]:
    roots: list[Path] = []
    for candidate in (_project_root(project_key), ROOT):
        resolved = candidate.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _read_package_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        record_honesty_event(
            component="project_dna_profiler",
            category="caught_error",
            operation="read_package_json",
            subject=str(path),
            reason="package manifest could not be parsed for project DNA dependency/framework signals",
            fallback="empty_package_manifest_for_this_root",
            claim_impact="project_dna_dependency_and_framework_detection_degraded",
            exception=exc,
        )
        return {}


def _read_package_json(root: Path) -> dict[str, Any]:
    return _read_package_manifest(root / "package.json")


def _workspace_patterns(package: dict[str, Any]) -> list[str]:
    workspaces = package.get("workspaces")
    if isinstance(workspaces, list):
        values = workspaces
    elif isinstance(workspaces, dict):
        values = workspaces.get("packages", [])
    else:
        values = []
    return [str(item).replace("\\", "/").strip() for item in values if str(item).strip()]


def _declared_package_manifests(root: Path, package: dict[str, Any]) -> list[Path]:
    resolved_root = root.resolve()
    manifests = [resolved_root / "package.json"]
    for pattern in _workspace_patterns(package):
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            record_honesty_event(
                component="project_dna_profiler",
                category="invalid_input",
                operation="expand_workspace_manifest",
                subject=pattern,
                reason="workspace pattern escapes or bypasses the declared project root",
                fallback="workspace_pattern_ignored",
                claim_impact="project_dna_dependency_and_framework_detection_degraded",
            )
            continue
        for match in resolved_root.glob(pattern):
            candidate = match if match.name == "package.json" else match / "package.json"
            try:
                candidate.resolve().relative_to(resolved_root)
            except ValueError:
                continue
            if candidate.is_file() and candidate not in manifests:
                manifests.append(candidate)
    return manifests


def _dependency_names(roots: list[Path]) -> set[str]:
    names: set[str] = set()
    seen_manifests: set[Path] = set()
    for root in roots:
        package = _read_package_json(root)
        for manifest in _declared_package_manifests(root, package):
            resolved_manifest = manifest.resolve()
            if resolved_manifest in seen_manifests:
                continue
            seen_manifests.add(resolved_manifest)
            manifest_package = package if resolved_manifest == (root.resolve() / "package.json") else _read_package_manifest(manifest)
            for section_name in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                section = manifest_package.get(section_name)
                if isinstance(section, dict):
                    names.update(str(item) for item in section.keys())
    return names


def _path_exists(root: Path, marker: str) -> bool:
    if marker.endswith("/"):
        return (root / marker.rstrip("/")).is_dir()
    return (root / marker).exists()


def _atlas_files(atlas: dict[str, Any], project_key: str) -> dict[str, Any]:
    projects = _as_dict(atlas.get("projects"))
    files = _as_dict(_as_dict(projects.get(project_key)).get("files"))
    if files:
        return files
    files = _as_dict(_as_dict(atlas.get(project_key)).get("files"))
    if files:
        return files
    if project_key == "MAIN":
        return _as_dict(atlas.get("files"))
    return {}


def _extension_counts(files: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for raw_path in files.keys():
        suffix = Path(str(raw_path).split("::")[-1]).suffix.lower()
        if suffix:
            counts[suffix] += 1
    return counts


def _detect_languages(policy: dict[str, Any], ext_counts: Counter[str]) -> list[dict[str, Any]]:
    weights = _as_dict(policy.get("confidence_weights"))
    ext_weight = float(weights.get("source_extension", 0.7) or 0.7)
    rows: list[dict[str, Any]] = []
    for language, extensions in _as_dict(policy.get("language_extensions")).items():
        matched = {ext: ext_counts.get(str(ext).lower(), 0) for ext in _as_list(extensions)}
        total = sum(matched.values())
        if total:
            rows.append(
                {
                    "id": language,
                    "confidence": ext_weight,
                    "evidence": [{"kind": "source_extension", "matches": matched, "total_files": total}],
                }
            )
    return sorted(rows, key=lambda row: (-float(row["confidence"]), str(row["id"])))


def _detect_package_managers(policy: dict[str, Any], roots: list[Path]) -> list[dict[str, Any]]:
    weight = float(_as_dict(policy.get("confidence_weights")).get("config_file", 0.85) or 0.85)
    rows: list[dict[str, Any]] = []
    for manager, markers in _as_dict(policy.get("package_managers")).items():
        hits = [
            _rel(root / str(marker))
            for root in roots
            for marker in _as_list(markers)
            if _path_exists(root, str(marker))
        ]
        if hits:
            rows.append({"id": manager, "confidence": weight, "evidence": [{"kind": "config_file", "matches": sorted(set(hits))}]})
    return sorted(rows, key=lambda row: (-float(row["confidence"]), str(row["id"])))


def _detect_frameworks(policy: dict[str, Any], roots: list[Path], deps: set[str]) -> list[dict[str, Any]]:
    weights = _as_dict(policy.get("confidence_weights"))
    package_weight = float(weights.get("package_dependency", 0.9) or 0.9)
    config_weight = float(weights.get("config_file", 0.85) or 0.85)
    rows: list[dict[str, Any]] = []
    package_signals = _as_dict(policy.get("framework_package_signals"))
    config_signals = _as_dict(policy.get("framework_config_signals"))
    framework_ids = sorted(set(package_signals) | set(config_signals))
    for framework in framework_ids:
        evidence: list[dict[str, Any]] = []
        confidence = 0.0
        package_hits = sorted({signal for signal in _as_list(package_signals.get(framework)) if str(signal) in deps})
        if package_hits:
            confidence = max(confidence, package_weight)
            evidence.append({"kind": "package_dependency", "matches": package_hits})
        config_hits = [
            _rel(root / str(marker))
            for root in roots
            for marker in _as_list(config_signals.get(framework))
            if _path_exists(root, str(marker))
        ]
        if config_hits:
            confidence = max(confidence, config_weight)
            evidence.append({"kind": "config_file", "matches": sorted(set(config_hits))})
        if evidence:
            rows.append({"id": framework, "confidence": round(confidence, 2), "evidence": evidence})
    return sorted(rows, key=lambda row: (-float(row["confidence"]), str(row["id"])))


def _detect_infrastructure(policy: dict[str, Any], roots: list[Path], ext_counts: Counter[str]) -> list[dict[str, Any]]:
    weight = float(_as_dict(policy.get("confidence_weights")).get("config_file", 0.85) or 0.85)
    rows: list[dict[str, Any]] = []
    for signal_id, markers in _as_dict(policy.get("infrastructure_file_signals")).items():
        hits: list[str] = []
        for root in roots:
            for marker in _as_list(markers):
                marker_text = str(marker)
                if marker_text == "proto":
                    if ext_counts.get(".proto", 0):
                        hits.append(f"atlas_extension:.proto:{ext_counts.get('.proto', 0)}")
                    continue
                if _path_exists(root, marker_text):
                    hits.append(_rel(root / marker_text))
        if hits:
            rows.append({"id": signal_id, "confidence": weight, "evidence": [{"kind": "config_file", "matches": sorted(set(hits))[:25]}]})
    return sorted(rows, key=lambda row: (-float(row["confidence"]), str(row["id"])))


def _repo_shape(policy: dict[str, Any], roots: list[Path], project_count: int) -> dict[str, Any]:
    weight = float(_as_dict(policy.get("confidence_weights")).get("workspace_marker", 0.85) or 0.85)
    markers = _as_dict(policy.get("repo_shape_markers"))
    monorepo_hits = [
        _rel(root / str(marker))
        for root in roots
        for marker in _as_list(markers.get("monorepo"))
        if (root / str(marker)).is_dir()
    ]
    workspace_files = [
        _rel(root / marker)
        for root in roots
        for marker in ("pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json")
        if (root / marker).exists()
    ]
    if monorepo_hits or workspace_files or project_count > 1:
        return {
            "id": "monorepo",
            "confidence": weight,
            "evidence": {
                "workspace_markers": sorted(set(monorepo_hits + workspace_files)),
                "project_count": project_count,
            },
        }
    single_hits = [
        _rel(root / str(marker))
        for root in roots
        for marker in _as_list(markers.get("single_app"))
        if (root / str(marker)).exists()
    ]
    return {"id": "single_project", "confidence": 0.7 if single_hits else 0.5, "evidence": {"markers": sorted(set(single_hits)), "project_count": project_count}}


def _activation_intents(policy: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    detected_ids = {
        row.get("id")
        for group_name in ("languages", "frameworks", "infrastructure")
        for row in _as_list(project.get(group_name))
        if isinstance(row, dict)
    }
    rows: list[dict[str, Any]] = []
    for capability, required_signals in _as_dict(policy.get("activation_intent")).items():
        hits = sorted(str(item) for item in _as_list(required_signals) if item in detected_ids)
        if hits:
            rows.append({"capability": capability, "status": "candidate", "matched_signals": hits})
    return sorted(rows, key=lambda row: str(row["capability"]))


def build_project_dna_profile() -> dict[str, Any]:
    policy = load_json_object_strict(POLICY_PATH, label="Project DNA profile policy")
    atlas = load_atlas_data()
    runtime_projects = resolve_runtime_projects(ROOT)
    project_count = len(runtime_projects)
    projects: list[dict[str, Any]] = []
    aggregate_languages: Counter[str] = Counter()
    aggregate_frameworks: Counter[str] = Counter()
    aggregate_infra: Counter[str] = Counter()

    for project_key in sorted(runtime_projects):
        roots = _candidate_roots(project_key)
        deps = _dependency_names(roots)
        files = _atlas_files(atlas, project_key)
        ext_counts = _extension_counts(files)
        languages = _detect_languages(policy, ext_counts)
        frameworks = _detect_frameworks(policy, roots, deps)
        package_managers = _detect_package_managers(policy, roots)
        infrastructure = _detect_infrastructure(policy, roots, ext_counts)
        row = {
            "project": project_key,
            "display_name": _as_dict(DYNAMIC_CONFIG.get("project_display_names")).get(project_key, project_key),
            "roots": [_rel(root) for root in roots],
            "repo_shape": _repo_shape(policy, roots, project_count),
            "source_file_count": sum(ext_counts.values()),
            "extension_counts": dict(ext_counts.most_common()),
            "languages": languages,
            "frameworks": frameworks,
            "package_managers": package_managers,
            "infrastructure": infrastructure,
        }
        row["activation_intents"] = _activation_intents(policy, row)
        projects.append(row)
        aggregate_languages.update(str(item.get("id")) for item in languages)
        aggregate_frameworks.update(str(item.get("id")) for item in frameworks)
        aggregate_infra.update(str(item.get("id")) for item in infrastructure)

    payload = {
        "meta": {
            "kind": "project_dna_profile",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.engines.project_dna_profiler",
            "policy": _rel(POLICY_PATH),
            "source_artifacts": ["config/codemaps.config.json", "config/codemaps.discovery.json", "output/.raw/atlas.json"],
            "scope": "descriptive_profile_only",
        },
        "summary": {
            "status": "PASS",
            "projects": len(projects),
            "languages": sorted(aggregate_languages),
            "frameworks": sorted(aggregate_frameworks),
            "infrastructure": sorted(aggregate_infra),
            "capability_activation_ready": True,
            "does_not_schedule_pipeline": True,
        },
        "projects": projects,
    }
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = _as_dict(payload.get("summary"))
    lines = [
        "# Project DNA Profile",
        "",
        "Policy-backed repository DNA summary for future capability activation planning.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- projects: `{summary.get('projects')}`",
        f"- languages: `{summary.get('languages')}`",
        f"- frameworks: `{summary.get('frameworks')}`",
        f"- infrastructure: `{summary.get('infrastructure')}`",
        f"- capability_activation_ready: `{summary.get('capability_activation_ready')}`",
        "",
        "| Project | Shape | Languages | Frameworks | Activation Candidates |",
        "|---|---|---|---|---|",
    ]
    for project in _as_list(payload.get("projects")):
        shape = _as_dict(project.get("repo_shape")).get("id")
        languages = ", ".join(str(item.get("id")) for item in _as_list(project.get("languages")) if isinstance(item, dict))
        frameworks = ", ".join(str(item.get("id")) for item in _as_list(project.get("frameworks")) if isinstance(item, dict))
        candidates = ", ".join(str(item.get("capability")) for item in _as_list(project.get("activation_intents")) if isinstance(item, dict))
        lines.append(f"| `{project.get('project')}` | `{shape}` | `{languages}` | `{frameworks}` | `{candidates}` |")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This artifact does not decide architecture and does not schedule engines.",
            "- Architecture confidence remains a post-Atlas Oracle responsibility.",
            "- The next layer should consume this profile with the capability registry to produce an explicit activation plan.",
        ]
    )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_project_dna_profile()
    save_json_atomic(RAW_DIR / "project_dna_profile.json", payload)
    save_text_atomic(REPORTS_DIR / "project_dna_profile.md", render_report(payload))
    return payload


if __name__ == "__main__":
    print(json.dumps(run().get("summary", {}), ensure_ascii=False))
