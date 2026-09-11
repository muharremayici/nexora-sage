from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _normalized(path: str) -> str:
    return str(path or "").replace("\\", "/").strip("/")


def _repository_label(repository_root: str, corpus_root: Path | None) -> str:
    root = Path(repository_root) if repository_root else None
    if root is None:
        return "unknown"
    if corpus_root is not None:
        try:
            relative = root.resolve().relative_to(corpus_root.resolve())
            parts = relative.parts
            if len(parts) >= 2 and parts[0] == "_current":
                return parts[1]
            return relative.as_posix()
        except (OSError, ValueError):
            pass
    return root.parent.name or root.name


def _canonical_family(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(value or "").lower())).strip("_")


def _runtime_run_id(path: Path) -> str:
    for parent in path.parents:
        if parent.name == "output":
            return parent.parent.name
    return ""


def _matched_roster_family(observation: dict[str, Any], roster_families: set[str]) -> str:
    candidates = {
        _canonical_family(str(observation.get(key) or ""))
        for key in ("repository_family", "repository_label", "runtime_run_id", "artifact_path")
    }
    candidates.discard("")
    matches = {
        family
        for family in roster_families
        if any(
            candidate == family
            or re.search(rf"(?:^|_){re.escape(family)}(?:_|$)", candidate)
            for candidate in candidates
        )
    }
    return max(matches, key=len) if matches else ""


def _classification_identity(projects: list[dict[str, Any]]) -> str:
    projection = [
        {
            "project": row.get("project"),
            "file_count": row.get("file_count"),
            "recommended_profile": row.get("recommended_profile"),
            "blueprint": row.get("blueprint"),
            "confidence": row.get("confidence"),
            "seal_ready": row.get("seal_ready"),
        }
        for row in projects
        if isinstance(row, dict)
    ]
    canonical = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _observed_runtime_trait(project: dict[str, Any]) -> dict[str, Any] | None:
    evidence = project.get("evidence") if isinstance(project.get("evidence"), dict) else {}
    nextjs = evidence.get("nextjs") if isinstance(evidence.get("nextjs"), dict) else {}
    app_files = int(nextjs.get("app_router_files") or 0)
    pages_files = int(nextjs.get("pages_router_files") or 0)
    route_files = int(nextjs.get("route_convention_files") or 0)
    boundary_markers = int(nextjs.get("next_boundary_markers") or 0)
    declared_runtime = str(nextjs.get("runtime_trait") or "")
    if declared_runtime in {"next_app_router", "next_pages_router", "next_hybrid"}:
        runtime = declared_runtime
    elif route_files <= 0 and boundary_markers <= 0:
        return None
    elif app_files > 0 and pages_files > 0:
        runtime = "next_hybrid"
    elif app_files > 0:
        runtime = "next_app_router"
    elif pages_files > 0:
        runtime = "next_pages_router"
    else:
        return None
    blueprint = project.get("blueprint") if isinstance(project.get("blueprint"), dict) else {}
    return {
        "project": str(project.get("project") or ""),
        "observed_runtime_trait": runtime,
        "blueprint_runtime": blueprint.get("runtime"),
        "preserved_by_blueprint": blueprint.get("runtime") == runtime,
        "evidence_basis": {
            "app_router_files": app_files,
            "pages_router_files": pages_files,
            "route_convention_files": route_files,
            "next_boundary_markers": boundary_markers,
        },
        "truth_boundary": "Oracle-internal consistency observation; source adjudication is still required.",
    }


def _taxonomy_observation(profile_registry: dict[str, Any]) -> dict[str, Any]:
    contract = profile_registry.get("blueprint_contract") if isinstance(profile_registry, dict) else {}
    contract = contract if isinstance(contract, dict) else {}
    axes = contract.get("axes") if isinstance(contract.get("axes"), dict) else {}
    runtime_axis = {str(item) for item in axes.get("runtime", [])}
    canonical = contract.get("canonical_profiles") if isinstance(contract.get("canonical_profiles"), dict) else {}
    represented = {
        str(profile.get("runtime"))
        for profile in canonical.values()
        if isinstance(profile, dict) and profile.get("runtime")
    }
    traits = contract.get("runtime_traits") if isinstance(contract.get("runtime_traits"), dict) else {}
    return {
        "runtime_axis_values": sorted(runtime_axis),
        "canonical_profile_runtime_values": sorted(represented),
        "declared_runtime_traits": sorted(str(item) for item in traits),
        "unrepresented_runtime_axis_values": sorted(runtime_axis - represented),
        "unrepresented_declared_runtime_traits": sorted(set(str(item) for item in traits) - represented),
        "status": "ATTENTION" if runtime_axis - represented else "COMPLETE",
    }


def _matching_run(path: Path, runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    artifact = os.path.normcase(os.path.abspath(path))
    matches = []
    for row in runs:
        runtime = str(row.get("runtime") or "")
        if not runtime:
            continue
        prefix = os.path.normcase(os.path.abspath(Path(runtime) / "output"))
        if artifact == prefix or artifact.startswith(prefix + os.sep):
            matches.append(row)
    return matches[0] if len(matches) == 1 else None


def _artifact_observation(
    path: Path,
    *,
    corpus_root: Path | None,
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    oracle = _load_json(path)
    oracle = oracle if isinstance(oracle, dict) else {}
    preflight_path = path.parent / "external_target_preflight.json"
    preflight = _load_json(preflight_path)
    preflight = preflight if isinstance(preflight, dict) else {}
    summary = preflight.get("summary") if isinstance(preflight.get("summary"), dict) else {}
    preflight_scope = summary.get("analysis_scope") if isinstance(summary.get("analysis_scope"), dict) else {}
    embedded_scope = oracle.get("scope_authority") if isinstance(oracle.get("scope_authority"), dict) else {}
    scope = {**preflight_scope, **embedded_scope}
    repository_root = str(preflight_scope.get("repository_root") or "")
    run = _matching_run(path, runs)
    run = run if isinstance(run, dict) else {}
    runtime_run_id = _runtime_run_id(path)
    projects = [row for row in oracle.get("projects", []) if isinstance(row, dict)]
    repository_blueprint = (
        oracle.get("repository_blueprint")
        if isinstance(oracle.get("repository_blueprint"), dict)
        else None
    )
    oracle_projects = sorted(str(row.get("project") or "") for row in projects)
    runtime_projects = scope.get("effective_runtime_projects")
    runtime_projects = runtime_projects if isinstance(runtime_projects, dict) else {}
    expected_projects = sorted(str(item) for item in runtime_projects)
    runtime_traits = [
        trait
        for project in projects
        if (trait := _observed_runtime_trait(project)) is not None
    ]
    topology = str(preflight_scope.get("discovered_topology") or "")
    topology_basis = "preflight"
    if not topology:
        topology = "multi_project" if len(expected_projects) > 1 else "single_project" if len(expected_projects) == 1 else "unknown"
        topology_basis = "effective_runtime_project_count" if expected_projects else "unknown"
    scope_sources = []
    if embedded_scope:
        scope_sources.append("oracle_embedded_scope_authority")
    if preflight_scope:
        scope_sources.append("adjacent_external_target_preflight")
    return {
        "artifact_path": path.as_posix(),
        "preflight_path": preflight_path.as_posix() if preflight_path.is_file() else None,
        "runtime_run_id": runtime_run_id,
        "generated_at": str((oracle.get("meta") or {}).get("generated_at") or ""),
        "repository_root": repository_root,
        "repository_label": str(
            run.get("repository")
            or (_repository_label(repository_root, corpus_root) if repository_root else "")
            or runtime_run_id
            or path.parent.parent.name
        ),
        "repository_family": str(run.get("repository") or ""),
        "subject": str(run.get("subject") or ""),
        "snapshot_fingerprint": str(run.get("fingerprint") or ""),
        "run_inventory_status": "BOUND" if run else "UNBOUND",
        "run_eligible": bool(run.get("eligible")) if run else None,
        "run_status": str(run.get("status") or ""),
        "scope_classification": str(run.get("scope_classification") or ""),
        "scope_evidence_sources": scope_sources,
        "scope_evidence_binding_status": "BOUND" if scope_sources else "UNBOUND",
        "scope_authority_id": str(scope.get("scope_authority_id") or ""),
        "classification_identity": _classification_identity(projects),
        "discovered_topology": topology,
        "discovered_topology_basis": topology_basis,
        "runtime_project_keys": expected_projects,
        "oracle_project_keys": oracle_projects,
        "project_set_status": (
            "MATCH"
            if expected_projects and expected_projects == oracle_projects
            else "NOT_COMPARABLE" if not expected_projects else "MISMATCH"
        ),
        "repository_level_blueprint_status": (
            f"PRESENT_{str(repository_blueprint.get('classification_status') or 'UNSPECIFIED')}"
            if repository_blueprint is not None
            else "NOT_REPRESENTED_FOR_MULTI_PROJECT" if topology == "multi_project" else "NOT_APPLICABLE"
        ),
        "repository_blueprint_classification_status": (
            str(repository_blueprint.get("classification_status") or "UNSPECIFIED")
            if repository_blueprint is not None
            else None
        ),
        "repository_blueprint_composition_model": (
            repository_blueprint.get("composition_model")
            if repository_blueprint is not None
            else None
        ),
        "repository_blueprint_relationship_model": (
            repository_blueprint.get("relationship_model")
            if repository_blueprint is not None
            else None
        ),
        "repository_blueprint_proposal_identity_status": (
            str(repository_blueprint.get("proposal_identity_status") or "UNSPECIFIED")
            if repository_blueprint is not None
            else None
        ),
        "project_count": len(projects),
        "profile_counts": dict(sorted(Counter(str(row.get("recommended_profile") or "UNKNOWN") for row in projects).items())),
        "zero_file_projects": sorted(
            str(row.get("project") or "")
            for row in projects
            if int(row.get("file_count") or 0) == 0
        ),
        "runtime_trait_observations": runtime_traits,
        "unpreserved_runtime_traits": [
            row for row in runtime_traits if not row["preserved_by_blueprint"]
        ],
        "oracle_scope_evidence_status": str((oracle.get("summary") or {}).get("scope_evidence_status") or "NOT_RECORDED"),
        "manual_disposition": "pending_source_adjudication",
    }


def build_corpus_matrix(
    inventory_path: Path,
    *,
    corpus_root: Path | None = None,
    profile_registry_path: Path | None = None,
    run_inventory_path: Path | None = None,
    coverage_summary_path: Path | None = None,
    artifact_roots: list[Path] | None = None,
) -> dict[str, Any]:
    inventory = _load_json(inventory_path)
    if not isinstance(inventory, list):
        raise ValueError("Inventory must be a JSON array.")
    candidates = {
        Path(str(row.get("path")))
        for row in inventory
        if isinstance(row, dict)
        and Path(str(row.get("path") or "")).name.lower() == "architecture_oracle.json"
    }
    for root in artifact_roots or []:
        if not root.is_dir():
            continue
        candidates.update(
            path
            for path in root.rglob("architecture_oracle.json")
            if path.parent.name == ".raw"
        )
    run_inventory = _load_json(run_inventory_path) if run_inventory_path and run_inventory_path.is_file() else []
    runs = [row for row in run_inventory if isinstance(row, dict)] if isinstance(run_inventory, list) else []
    observations = [
        _artifact_observation(path, corpus_root=corpus_root, runs=runs)
        for path in sorted(candidates, key=lambda item: item.as_posix().lower())
        if path.is_file()
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in observations:
        identity = (
            row["repository_root"]
            or row["snapshot_fingerprint"]
            or row["scope_authority_id"]
            or row["artifact_path"]
        )
        grouped.setdefault(identity, []).append(row)

    repositories = []
    for identity, rows in sorted(grouped.items(), key=lambda item: (item[1][0]["repository_label"], item[0])):
        rows.sort(key=lambda row: (row["generated_at"], row["artifact_path"]))
        current = dict(rows[-1])
        current["repository_identity"] = identity
        current["observation_count"] = len(rows)
        current["classification_identities"] = sorted({row["classification_identity"] for row in rows})
        current["classification_stability"] = (
            "STABLE" if len(current["classification_identities"]) == 1 else "CHANGED_ACROSS_RUNS"
        )
        current["superseded_artifacts"] = [row["artifact_path"] for row in rows[:-1]]
        repositories.append(current)

    taxonomy = _taxonomy_observation(
        _load_json(profile_registry_path) if profile_registry_path and profile_registry_path.is_file() else {}
    )
    coverage_summary = (
        _load_json(coverage_summary_path)
        if coverage_summary_path and coverage_summary_path.is_file()
        else {}
    )
    coverage_summary = coverage_summary if isinstance(coverage_summary, dict) else {}
    controlled_repositories = sorted(
        {str(row.get("repository")) for row in runs if str(row.get("repository") or "").strip()}
    )
    outside_controlled = sorted(
        str(item)
        for item in coverage_summary.get("outside_controlled_campaigns", [])
        if str(item).strip()
    )
    roster_families = {
        _canonical_family(item)
        for item in controlled_repositories + outside_controlled
    }
    matched_artifact_families = {
        family
        for row in observations
        if (family := _matched_roster_family(row, roster_families))
    }
    unmapped_artifact_families = {
        _canonical_family(str(row.get("repository_family") or row.get("repository_label") or ""))
        for row in observations
        if not _matched_roster_family(row, roster_families)
        and str(row.get("repository_family") or row.get("repository_label") or "").strip()
    }
    artifact_families = sorted(matched_artifact_families | unmapped_artifact_families)
    covered_roster_families = roster_families.intersection(artifact_families)
    controlled_family_map = {_canonical_family(item): item for item in controlled_repositories}
    outside_family_map = {_canonical_family(item): item for item in outside_controlled}
    total_corpus_repositories = len(controlled_repositories) + len(outside_controlled)
    return {
        "meta": {
            "kind": "architecture_blueprint_corpus_applicability_matrix",
            "version": "v1",
            "source_inventory": inventory_path.as_posix(),
            "truth_boundary": (
                "This matrix checks preserved artifact coverage and internal representability. "
                "It does not certify architectural correctness without source-grounded adjudication."
            ),
        },
        "summary": {
            "inventory_artifacts": len(candidates),
            "readable_artifacts": len(observations),
            "unique_repository_snapshots": len(repositories),
            "repository_families_with_architecture_artifacts": len(covered_roster_families),
            "controlled_repository_families": len(controlled_repositories),
            "outside_controlled_repository_families": len(outside_controlled),
            "total_corpus_repository_families": total_corpus_repositories,
            "controlled_repositories_without_architecture_artifacts": sorted(
                original
                for canonical, original in controlled_family_map.items()
                if canonical not in artifact_families
            ),
            "outside_controlled_repositories_without_bound_architecture_artifacts": sorted(
                original
                for canonical, original in outside_family_map.items()
                if canonical not in artifact_families
            ),
            "unmapped_artifact_repository_families": sorted(
                unmapped_artifact_families
            ),
            "artifacts_without_scope_evidence": sum(
                row["scope_evidence_binding_status"] == "UNBOUND" for row in observations
            ),
            "duplicate_observations": len(observations) - len(repositories),
            "repositories_with_project_set_mismatch": sum(row["project_set_status"] == "MISMATCH" for row in repositories),
            "repositories_with_zero_file_projects": sum(bool(row["zero_file_projects"]) for row in repositories),
            "multi_project_repositories_without_repository_blueprint": sum(
                row["repository_level_blueprint_status"] == "NOT_REPRESENTED_FOR_MULTI_PROJECT"
                for row in repositories
            ),
            "projects_with_unpreserved_runtime_traits": sum(
                len(row["unpreserved_runtime_traits"]) for row in repositories
            ),
            "manual_adjudication_status": "PENDING",
        },
        "taxonomy_observation": taxonomy,
        "repositories": repositories,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    taxonomy = payload["taxonomy_observation"]
    lines = [
        "# Architecture Blueprint Corpus Applicability Matrix",
        "",
        payload["meta"]["truth_boundary"],
        "",
        "## Summary",
        "",
        f"- Preserved Oracle artifacts: `{summary['readable_artifacts']}/{summary['inventory_artifacts']}`",
        f"- Unique repository snapshots: `{summary['unique_repository_snapshots']}`",
        f"- Repository families with Architecture Oracle artifacts: `{summary['repository_families_with_architecture_artifacts']}/{summary['total_corpus_repository_families']}`",
        f"- Controlled repository families: `{summary['controlled_repository_families']}`",
        f"- Outside controlled campaigns: `{summary['outside_controlled_repository_families']}`",
        f"- Controlled repositories missing Architecture Oracle artifacts: `{summary['controlled_repositories_without_architecture_artifacts']}`",
        f"- Duplicate observations: `{summary['duplicate_observations']}`",
        f"- Project-set mismatches: `{summary['repositories_with_project_set_mismatch']}`",
        f"- Repositories with zero-file projects: `{summary['repositories_with_zero_file_projects']}`",
        f"- Multi-project repositories without a repository-level blueprint: `{summary['multi_project_repositories_without_repository_blueprint']}`",
        f"- Projects whose observed runtime trait is not preserved by the selected blueprint: `{summary['projects_with_unpreserved_runtime_traits']}`",
        f"- Unrepresented runtime axis values: `{taxonomy['unrepresented_runtime_axis_values']}`",
        "",
        "## Repository Matrix",
        "",
        "| Repository | Projects | Profiles | Scope | Repo Blueprint | Zero-file | Runtime gaps | Runs |",
        "|---|---:|---|---|---|---:|---:|---:|",
    ]
    for row in payload["repositories"]:
        lines.append(
            f"| `{row['repository_label']}` | {row['project_count']} | "
            f"`{row['profile_counts']}` | `{row['project_set_status']}` | "
            f"`{row['repository_level_blueprint_status']}` | {len(row['zero_file_projects'])} | "
            f"{len(row['unpreserved_runtime_traits'])} | {row['observation_count']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A runtime gap is an Oracle-internal representability mismatch, not yet a source-adjudicated false classification.",
            "- A missing repository-level blueprint means project blueprints do not describe the host/companion composition.",
            "- Manual adjudication must sample each observed profile and every mismatch family before implementation changes are frozen.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a no-replay corpus matrix from preserved Architecture Oracle artifacts.")
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path)
    parser.add_argument("--profile-registry", type=Path)
    parser.add_argument("--run-inventory", type=Path)
    parser.add_argument("--coverage-summary", type=Path)
    parser.add_argument("--artifact-root", type=Path, action="append", default=[])
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-markdown", type=Path)
    args = parser.parse_args()
    payload = build_corpus_matrix(
        args.inventory,
        corpus_root=args.corpus_root,
        profile_registry_path=args.profile_registry,
        run_inventory_path=args.run_inventory,
        coverage_summary_path=args.coverage_summary,
        artifact_roots=args.artifact_root,
    )
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.output_markdown:
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
