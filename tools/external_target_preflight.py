from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import (
    CODE_MAPS_DIR,
    CONFIG_DIR,
    DOCTRINE,
    _infer_target_architecture,
    _infer_target_bundler,
    _infer_target_path_aliases,
    _infer_target_plugins_from_inventory,
    _observe_target_path_aliases_from_files,
    _target_dependency_names,
    external_target_repository_topology,
    external_target_scope_projection,
    save_json_atomic,
    save_text_atomic,
)
from tools.core.unmanaged_atomic_io import native_filesystem_path
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.language_registry import (
    config_file_marker_map,
    language_registry_provenance,
    manifest_file_marker_map,
    observation_only_extension_language_map,
    skip_dirs,
)
from tools.core.installation_identity import runtime_installation_excluded_roots
from tools.core.target_inventory import (
    TARGET_OBSERVATION_IDENTITY_ALGORITHM,
    repository_and_selected_project_inventory,
)
from tools.core.repository_topology import classify_project_system_kinds
from tools.core.analysis_scope_authority import (
    INCOMPLETE_EVIDENCE,
    build_preflight_scope_authority,
    runtime_project_projection,
)
from tools.core.target_policy_profile import (
    aggregate_effective_target_policy,
    inventory_project_target_policy,
)
from tools.core.target_repository_trust import (
    is_target_path_contained,
    new_target_path_boundary_state,
    target_trust_projection,
)


EXTERNAL_TARGETS_DIR = CODE_MAPS_DIR / "output" / "external_targets"
POLICY_PATH = CONFIG_DIR / "external_target_preflight_policy.json"
POLYGLOT_CAPABILITIES_PATH = CONFIG_DIR / "polyglot_capabilities.json"
FAIL_CLOSED_STATUS = "FAIL"


def _preflight_policy_issues(
    policy: dict[str, Any],
    capabilities: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    claim_levels = {
        str(value)
        for value in capabilities.get("claim_level_order", [])
        if str(value)
    }
    language_capabilities = capabilities.get("languages", {})
    if not claim_levels or not isinstance(language_capabilities, dict):
        issues.append("invalid_polyglot_capability_contract")

    authority = policy.get("analysis_authority")
    if not isinstance(authority, dict):
        issues.append("missing_analysis_authority")
    else:
        depth_labels = authority.get("depth_labels_by_effective_claim_level")
        if not isinstance(depth_labels, dict) or set(depth_labels) != claim_levels:
            issues.append("analysis_depth_labels_do_not_cover_claim_levels")
        fallback = authority.get("fallback_claim_level_without_react_signal")
        if fallback not in claim_levels:
            issues.append("invalid_non_react_fallback_claim_level")
        declared_specialists = {
            str(language)
            for language, payload in language_capabilities.items()
            if isinstance(payload, dict) and payload.get("claim_level") == "deep_specialist"
        }
        required_signal_languages = {
            str(value)
            for value in authority.get("deep_specialist_requires_react_signal_languages", [])
        }
        if required_signal_languages != declared_specialists:
            issues.append("react_signal_specialist_languages_do_not_match_capabilities")
        for field in (
            "react_typescript_depth_label",
            "react_effective_scope_label",
            "language_family_effective_scope_label",
            "unresolved_depth_label",
        ):
            if not str(authority.get(field) or "").strip():
                issues.append(f"missing_{field}")

    framework_evidence = policy.get("framework_source_evidence")
    validation_contract = policy.get("validation_contract", {})
    required_frameworks = set(framework_evidence) if isinstance(framework_evidence, dict) else set()
    allowed_framework_claims = {
        str(value) for value in validation_contract.get("allowed_framework_claim_levels", [])
    }
    specialist_framework = str(validation_contract.get("specialist_framework_family") or "")
    specialist_claim = str(validation_contract.get("specialist_framework_claim_level") or "")
    unsupported_claim = str(validation_contract.get("unsupported_framework_claim_level") or "")
    if (
        not required_frameworks
        or not allowed_framework_claims
        or specialist_framework not in required_frameworks
        or specialist_claim not in allowed_framework_claims
        or unsupported_claim not in allowed_framework_claims
    ):
        issues.append("invalid_framework_validation_contract")
    if not isinstance(framework_evidence, dict) or not required_frameworks:
        issues.append("invalid_framework_source_evidence_families")
    elif any(
        not isinstance(framework_evidence.get(framework), dict)
        or not framework_evidence[framework].get("package_names")
        or framework_evidence[framework].get("effective_claim_level") not in allowed_framework_claims
        or (
            framework == specialist_framework
            and framework_evidence[framework].get("effective_claim_level") != specialist_claim
        )
        or (
            framework != specialist_framework
            and framework_evidence[framework].get("effective_claim_level") != unsupported_claim
        )
        for framework in required_frameworks
    ):
        issues.append("invalid_framework_source_evidence_contract")
    react_evidence = framework_evidence.get(specialist_framework) if isinstance(framework_evidence, dict) else None
    if not isinstance(react_evidence, dict):
        issues.append("missing_react_framework_source_evidence")
    else:
        for field in ("package_names", "source_extensions", "excluded_path_segments"):
            values = react_evidence.get(field)
            if not isinstance(values, list) or not values or any(not str(value).strip() for value in values):
                issues.append(f"invalid_react_framework_{field}")

    expected_inventory_precedence = [
        "configured_manifest",
        "configured_configuration",
        "analysis_source",
        "observed_non_analysis_language",
        "known_non_source_template",
        "runtime_state",
        "unclassified",
    ]
    inventory_classification = policy.get("inventory_classification")
    runtime_state = (
        inventory_classification.get("runtime_state")
        if isinstance(inventory_classification, dict)
        else None
    )
    inventory_classification_valid = (
        isinstance(inventory_classification, dict)
        and inventory_classification.get("contract")
        == "bounded_external_inventory_disposition_v1"
        and inventory_classification.get("precedence") == expected_inventory_precedence
        and isinstance(runtime_state, dict)
        and all(
            isinstance(runtime_state.get(field), list)
            for field in ("file_extensions", "compound_suffixes", "file_name_patterns")
        )
        and all(
            str(value).startswith(".")
            for field in ("file_extensions", "compound_suffixes")
            for value in runtime_state.get(field, [])
        )
        and isinstance(inventory_classification.get("unclassified_example_limit"), int)
        and 0 < inventory_classification["unclassified_example_limit"] <= 1000
        and inventory_classification.get("unclassified_semantics")
        == "not_matched_by_configured_taxonomies_not_unsupported_source"
        and inventory_classification.get("excluded_file_count_status")
        == "unavailable_pruned_not_walked"
        and inventory_classification.get("decision_effect") == "observability_only"
    )
    if not inventory_classification_valid:
        issues.append("invalid_inventory_classification_contract")

    expected_dependency_sections = {
        "runtime": "dependencies",
        "development": "devDependencies",
        "peer": "peerDependencies",
        "optional": "optionalDependencies",
    }
    if policy.get("node_dependency_sections") != expected_dependency_sections:
        issues.append("invalid_node_dependency_sections")

    expected_installation_exclusion = {
        "mode": "resolved_runtime_installation_root",
        "directory_name_matching": False,
        "self_target_behavior": "include",
    }
    if policy.get("installation_root_exclusion") != expected_installation_exclusion:
        issues.append("invalid_installation_root_exclusion")
    if policy.get("additional_skip_dirs"):
        issues.append("directory_name_based_installation_exclusion_forbidden")

    status_policy = policy.get("status_policy")
    expected_status_policy = {
        "invalid_target": "FAIL",
        "recognized_language_or_react_signal": "PASS",
        "recognized_with_unsupported_families": "ATTENTION",
        "readable_but_no_recognized_signal": "ATTENTION",
    }
    if status_policy != expected_status_policy:
        issues.append("invalid_status_policy")
    return issues


def _slug_for_target(target_root: Path) -> str:
    from tools.core.config import _target_output_slug

    return _target_output_slug(str(target_root.resolve()))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _analysis_authority(
    language_counts: dict[str, int],
    has_react_signal: bool,
    policy: dict[str, Any],
    capabilities: dict[str, Any],
) -> dict[str, Any]:
    authority_policy = policy.get("analysis_authority", {})
    language_capabilities = capabilities.get("languages", {})
    claim_order = [str(value) for value in capabilities.get("claim_level_order", [])]
    rank = {claim_level: index for index, claim_level in enumerate(claim_order)}
    ceilings = {
        language: str(language_capabilities.get(language, {}).get("claim_level") or "not_available")
        for language in sorted(language_counts)
    }
    known_ceilings = [value for value in ceilings.values() if value in rank]
    ceiling = max(known_ceilings, key=rank.__getitem__) if known_ceilings else "not_available"
    effective = ceiling
    basis = "language_capability_ceiling"

    if has_react_signal:
        effective = "deep_specialist"
        basis = "react_signal_and_typescript_javascript_specialist_capability"
    elif ceiling == "deep_specialist":
        requiring_signal = {
            str(value)
            for value in authority_policy.get("deep_specialist_requires_react_signal_languages", [])
        }
        deepest_languages = {language for language, value in ceilings.items() if value == "deep_specialist"}
        if deepest_languages and deepest_languages <= requiring_signal:
            effective = str(authority_policy.get("fallback_claim_level_without_react_signal") or "not_available")
            basis = "deep_specialist_ceiling_reduced_without_required_react_signal"

    depth_labels = authority_policy.get("depth_labels_by_effective_claim_level", {})
    if has_react_signal:
        depth = str(authority_policy.get("react_typescript_depth_label") or "react_typescript_deep")
    else:
        depth = str(
            depth_labels.get(effective)
            or authority_policy.get("unresolved_depth_label")
            or "recognized_language_claim_not_available"
        )
    return {
        "status": "resolved" if effective in rank else "not_available",
        "analysis_depth": depth,
        "effective_claim_level": effective,
        "ceiling_claim_level": ceiling,
        "language_claim_level_ceilings": ceilings,
        "resolution_basis": basis,
        "effective_claim_scope": str(
            authority_policy.get("react_effective_scope_label")
            if has_react_signal
            else authority_policy.get("language_family_effective_scope_label")
        ),
        "unsupported_language_families": sorted(
            language
            for language, claim_level in ceilings.items()
            if claim_level not in rank
        ),
        "claim_level_is_ceiling_not_entitlement": True,
    }


def _dependency_names_by_section(
    package_payload: Any,
    section_policy: dict[str, Any],
) -> dict[str, set[str]]:
    groups = {str(label): set() for label in section_policy}
    if not isinstance(package_payload, dict):
        return groups
    for label, package_key in section_policy.items():
        value = package_payload.get(str(package_key))
        if isinstance(value, dict):
            groups[str(label)].update(str(name) for name in value)
    return groups


def _all_dependency_names(groups: dict[str, set[str]]) -> set[str]:
    return set().union(*groups.values()) if groups else set()


def _package_signal_sources(
    package_payload: Any,
    manifest_label: str,
    section_policy: dict[str, Any],
) -> dict[str, set[str]]:
    """Preserve whether a framework package was observed as identity or dependency."""
    if not isinstance(package_payload, dict):
        return {}
    signals: dict[str, set[str]] = {}
    package_name = str(package_payload.get("name") or "").strip()
    if package_name:
        signals.setdefault(package_name, set()).add(f"{manifest_label}#name")
    for label, package_key in section_policy.items():
        declared = package_payload.get(str(package_key))
        if not isinstance(declared, dict):
            continue
        for name in declared:
            signals.setdefault(str(name), set()).add(f"{manifest_label}#{label}")
    return signals


def _merge_package_signal_sources(
    target: dict[str, set[str]],
    incoming: dict[str, set[str]],
) -> None:
    for name, sources in incoming.items():
        target.setdefault(name, set()).update(sources)


def _framework_authority(
    package_signal_sources: dict[str, set[str]],
    policy: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    observed: dict[str, dict[str, Any]] = {}
    unsupported: list[str] = []
    for framework, payload in policy.get("framework_source_evidence", {}).items():
        declared_packages = {str(value) for value in payload.get("package_names", [])}
        matched_packages = sorted(set(package_signal_sources) & declared_packages)
        if not matched_packages:
            continue
        claim_level = str(payload.get("effective_claim_level") or "not_available")
        observed[str(framework)] = {
            "status": "observed_from_declared_package_identity_or_dependency",
            "matched_packages": matched_packages,
            "matched_package_sources": {
                name: sorted(package_signal_sources.get(name, set()))
                for name in matched_packages
            },
            "effective_claim_level": claim_level,
        }
        if claim_level == "not_available":
            unsupported.append(str(framework))
    return dict(sorted(observed.items())), sorted(unsupported)


def _package_react_ownership(
    target: Path,
    package_json: dict[str, Any],
    workspace_package_files: list[Path],
    node_dependency_sections: dict[str, Any],
    policy: dict[str, Any],
) -> dict[Path, bool]:
    react_packages = {
        str(value)
        for value in policy.get("framework_source_evidence", {}).get("react", {}).get("package_names", [])
    }
    manifests = [(target / "package.json", package_json)] + [
        (package_file, load_json_file(package_file, {}))
        for package_file in workspace_package_files
    ]
    return {
        manifest.parent.resolve(): bool(
            set(_package_signal_sources(payload, manifest.relative_to(target).as_posix(), node_dependency_sections))
            & react_packages
        )
        for manifest, payload in manifests
        if manifest.is_file()
    }


def _workspace_package_jsons(
    target: Path,
    package_json: dict[str, Any],
    limit: int = 250,
    *,
    path_boundary_state: dict[str, Any] | None = None,
) -> list[Path]:
    workspaces = package_json.get("workspaces") if isinstance(package_json, dict) else None
    patterns: list[str] = []
    if isinstance(workspaces, list):
        patterns = [str(item) for item in workspaces if isinstance(item, str)]
    elif isinstance(workspaces, dict) and isinstance(workspaces.get("packages"), list):
        patterns = [str(item) for item in workspaces.get("packages") if isinstance(item, str)]

    package_files: list[Path] = []
    skip_parts = {".git", "node_modules", "dist", "build", ".next", "coverage"}
    for pattern in patterns:
        normalized = pattern.rstrip("/\\")
        if not normalized or normalized.startswith("!"):
            continue
        candidates = list(target.glob(f"{normalized}/package.json"))
        if not candidates and ("*" in normalized or "?" in normalized):
            candidates = list(target.glob(f"{normalized}/**/package.json"))
        for candidate in candidates:
            if any(part in skip_parts for part in candidate.parts):
                continue
            if not is_target_path_contained(target, candidate, state=path_boundary_state):
                continue
            if candidate.is_file() and candidate not in package_files:
                package_files.append(candidate)
                if len(package_files) >= limit:
                    return package_files
    return package_files


def _node_dependency_evidence(
    target: Path,
    manifest_files: list[str],
    package_dependency_groups: dict[str, set[str]],
    workspace_package_files: list[Path],
    workspace_dependency_groups: dict[str, set[str]],
) -> dict[str, Any]:
    if not (target / "package.json").is_file():
        if manifest_files:
            return {
                "status": "not_evaluated",
                "parser": None,
                "manifest_files": manifest_files,
                "reason": "root_package_json_not_present_nested_node_manifests_not_evaluated",
            }
        return {
            "status": "not_present",
            "parser": None,
            "manifest_files": manifest_files,
            "reason": "package_json_not_present",
        }
    evaluated_manifest_files = ["package.json"] + sorted(
        path.relative_to(target).as_posix()
        for path in workspace_package_files
    )
    return {
        "status": "observed",
        "parser": "package_json",
        "manifest_files": manifest_files,
        "evaluated_manifest_files": evaluated_manifest_files,
        "unevaluated_manifest_files": sorted(set(manifest_files) - set(evaluated_manifest_files)),
        "dependency_counts_by_section": {
            label: len(names)
            for label, names in package_dependency_groups.items()
        },
        "all_declared_dependency_name_count": len(_all_dependency_names(package_dependency_groups)),
        "workspace_manifest_count": len(workspace_package_files),
        "workspace_dependency_counts_by_section": {
            label: len(names)
            for label, names in workspace_dependency_groups.items()
        },
        "workspace_all_declared_dependency_name_count": len(_all_dependency_names(workspace_dependency_groups)),
    }


def _embedded_sage_roots(target: Path) -> set[Path]:
    return runtime_installation_excluded_roots(target, CODE_MAPS_DIR)


def _outside_excluded_roots(path: Path, excluded_roots: set[Path]) -> bool:
    resolved = path.resolve()
    return not any(resolved == root or root in resolved.parents for root in excluded_roots)


def build_preflight(
    target_root: str | Path,
    *,
    projects: str | None = None,
    trust_class: str | None = None,
) -> dict[str, Any]:
    policy = load_json_object_strict(POLICY_PATH, label="External target preflight policy")
    polyglot_capabilities = load_json_object_strict(
        POLYGLOT_CAPABILITIES_PATH,
        label="Polyglot capabilities",
    )
    policy_issues = _preflight_policy_issues(policy, polyglot_capabilities)
    registry_provenance = language_registry_provenance()
    target = Path(target_root).expanduser()
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    else:
        target = target.resolve()
    path_boundary_state = new_target_path_boundary_state()
    embedded_sage_roots = _embedded_sage_roots(target)
    target_dependencies = _target_dependency_names(target)
    target_bundler = _infer_target_bundler(target, target_dependencies)
    target_architecture = _infer_target_architecture(target, target_dependencies, target_bundler)
    scope_projection = external_target_scope_projection(target, target_architecture)
    repository_topology = external_target_repository_topology(
        target,
        scope_projection,
        path_boundary_state=path_boundary_state,
    )
    runtime_projection = runtime_project_projection(repository_topology, projects)
    requested_project_filter = runtime_projection["requested_project_filter"]
    effective_runtime_projects = runtime_projection["effective_runtime_projects"]
    unavailable_requested_projects = runtime_projection["unavailable_requested_projects"]
    root_package_file = target / "package.json"
    package_json = (
        load_json_file(root_package_file, {})
        if is_target_path_contained(target, root_package_file, state=path_boundary_state)
        else {}
    )
    package_payload_cache = {
        (target / "package.json").resolve(): package_json,
    }

    def package_payload(package_file: Path) -> Any:
        if not is_target_path_contained(target, package_file, state=path_boundary_state):
            return {}
        resolved = package_file.resolve()
        if resolved not in package_payload_cache:
            package_payload_cache[resolved] = load_json_file(package_file, {})
        return package_payload_cache[resolved]

    node_dependency_sections = policy.get("node_dependency_sections", {})
    package_dependency_groups = _dependency_names_by_section(package_json, node_dependency_sections)
    workspace_package_files = [
        package_file
        for package_file in _workspace_package_jsons(
            target,
            package_json if isinstance(package_json, dict) else {},
            path_boundary_state=path_boundary_state,
        )
        if _outside_excluded_roots(package_file, embedded_sage_roots)
    ]
    package_react_ownership = _package_react_ownership(
        target,
        package_json if isinstance(package_json, dict) else {},
        workspace_package_files,
        node_dependency_sections,
        policy,
    )
    selected_package_files: list[Path] = []
    package_roots = sorted(package_react_ownership, key=lambda path: len(path.parts), reverse=True)
    for relative_path in effective_runtime_projects.values():
        project_root = target if str(relative_path) == "." else target / str(relative_path)
        owning_package_root = next(
            (
                package_root
                for package_root in package_roots
                if project_root.resolve() == package_root
                or project_root.resolve().is_relative_to(package_root)
            ),
            project_root.resolve(),
        )
        package_file = owning_package_root / "package.json"
        if (
            package_file.is_file()
            and _outside_excluded_roots(package_file, embedded_sage_roots)
            and package_file not in selected_package_files
        ):
            selected_package_files.append(package_file)
    react_packages = {
        str(value)
        for value in policy.get("framework_source_evidence", {}).get("react", {}).get("package_names", [])
    }
    for package_file in selected_package_files:
        selected_payload = package_payload(package_file)
        package_react_ownership[package_file.parent.resolve()] = bool(
            set(
                _package_signal_sources(
                    selected_payload,
                    package_file.relative_to(target).as_posix(),
                    node_dependency_sections,
                )
            )
            & react_packages
        )
    workspace_dependency_groups = {
        str(label): set()
        for label in node_dependency_sections
    }
    repository_package_signal_sources = _package_signal_sources(
        package_json,
        "package.json",
        node_dependency_sections,
    )
    for package_file in workspace_package_files:
        workspace_payload = package_payload(package_file)
        package_groups = _dependency_names_by_section(workspace_payload, node_dependency_sections)
        for label, names in package_groups.items():
            workspace_dependency_groups[label].update(names)
        _merge_package_signal_sources(
            repository_package_signal_sources,
            _package_signal_sources(
                workspace_payload,
                package_file.relative_to(target).as_posix(),
                node_dependency_sections,
            ),
        )
    effective_package_signal_sources: dict[str, set[str]] = {}
    for package_file in selected_package_files:
        _merge_package_signal_sources(
            effective_package_signal_sources,
            _package_signal_sources(
                package_payload(package_file),
                package_file.relative_to(target).as_posix(),
                node_dependency_sections,
            ),
        )
    config_marker_groups = config_file_marker_map()
    config_patterns = sorted({
        name
        for names in config_marker_groups.values()
        for name in names
    })
    manifest_patterns = manifest_file_marker_map()
    all_package_names = set(effective_package_signal_sources)
    framework_authority, unsupported_framework_families = _framework_authority(
        effective_package_signal_sources,
        policy,
    )
    repository_framework_authority, repository_unsupported_framework_families = _framework_authority(
        repository_package_signal_sources,
        policy,
    )
    target_observation: dict[str, Any] = {}
    inventory_classification: dict[str, Any] = {}
    repository_inventory, selected_inventory = repository_and_selected_project_inventory(
        target,
        repository_topology,
        policy,
        config_patterns,
        manifest_patterns,
        package_react_ownership,
        excluded_roots=embedded_sage_roots,
        projects=effective_runtime_projects,
        observation_state=target_observation,
        classification_projection=inventory_classification,
        path_boundary_state=path_boundary_state,
    ) if target.exists() and target.is_dir() else (
        (0, False, {}, {}, 0, 0, [], {}),
        (0, False, {}, {}, 0, 0, [], {}, {}, {}),
    )
    if not inventory_classification:
        classification_policy = policy.get("inventory_classification", {})
        unavailable_projection = {
            "status": "not_available",
            "reason": "invalid_target",
            "observed_file_count": 0,
            "classified_file_count": 0,
            "unclassified_file_count": 0,
            "disposition_counts": {},
            "unclassified_extension_counts": {},
            "unclassified_examples": [],
            "unclassified_example_limit": 0,
            "unclassified_examples_omitted": 0,
            "unclassified_semantics": (
                classification_policy.get("unclassified_semantics")
                if isinstance(classification_policy, dict)
                else None
            ),
            "excluded_file_count": None,
            "excluded_file_count_status": "unavailable_pruned_not_walked",
            "decision_effect": "observability_only",
        }
        inventory_classification = {
            "contract": (
                classification_policy.get("contract")
                if isinstance(classification_policy, dict)
                else None
            ),
            "precedence": list(
                classification_policy.get("precedence") or []
            ) if isinstance(classification_policy, dict) else [],
            "decision_effect": "observability_only",
            "repository": dict(unavailable_projection),
            "effective_scope": dict(unavailable_projection),
        }
    if not target_observation:
        target_observation = {
            "algorithm": TARGET_OBSERVATION_IDENTITY_ALGORITHM,
            "status": "incomplete",
            "fingerprint": None,
            "entry_count": 0,
            "decision_file_count": 0,
            "observation_contract_sha256": None,
            "error": "invalid_target",
        }
    (
        file_count,
        truncated,
        repository_language_counts,
        repository_analysis_language_counts,
        repository_react_source_files,
        repository_react_fixture_source_files,
        present_config_files,
        present_manifests,
    ) = repository_inventory
    (
        analysis_file_count,
        analysis_truncated,
        language_counts,
        analysis_language_counts,
        react_source_files,
        react_fixture_source_files,
        analysis_config_files,
        _analysis_manifest_files,
        analysis_project_file_counts,
        analysis_project_inventory_evidence,
    ) = selected_inventory
    project_manifest_payloads: dict[str, dict[str, Any]] = {}
    for project_key, relative_path in effective_runtime_projects.items():
        project_root = target if str(relative_path) == "." else target / str(relative_path)
        payload = package_payload(project_root / "package.json")
        project_manifest_payloads[str(project_key)] = payload if isinstance(payload, dict) else {}
    system_kind_policy = (
        DOCTRINE.get("discovery_project_system_kind_policy", {})
        if isinstance(DOCTRINE, dict)
        else {}
    )
    repository_topology["project_candidate_system_kinds"] = classify_project_system_kinds(
        analysis_project_inventory_evidence,
        project_manifest_payloads,
        system_kind_policy,
    )
    repository_topology["system_kind_contract"] = str(
        system_kind_policy.get("contract") or "technical_system_kind_unresolved_v1"
    )
    has_react_signal = bool(all_package_names & react_packages) or react_source_files > 0
    scope_authority = build_preflight_scope_authority(
        topology=repository_topology,
        projects=requested_project_filter,
        repository_file_count=file_count,
        repository_inventory_truncated=truncated,
        repository_language_counts=repository_language_counts,
        effective_file_count=analysis_file_count,
        effective_inventory_truncated=analysis_truncated,
        effective_language_counts=language_counts,
        effective_project_file_counts=analysis_project_file_counts,
        polyglot_capabilities=polyglot_capabilities,
        repository_analysis_language_counts=repository_analysis_language_counts,
        effective_analysis_language_counts=analysis_language_counts,
        effective_project_inventory_evidence=analysis_project_inventory_evidence,
    )
    status_policy = policy.get("status_policy", {})
    if policy_issues:
        status = FAIL_CLOSED_STATUS
    elif not target.exists() or not target.is_dir():
        status = str(status_policy["invalid_target"])
    elif scope_projection.get("status") != "resolved":
        status = FAIL_CLOSED_STATUS
    elif registry_provenance.get("source") != "configured":
        status = FAIL_CLOSED_STATUS
    elif has_react_signal or language_counts:
        status = str(status_policy["recognized_language_or_react_signal"])
    else:
        status = str(status_policy["readable_but_no_recognized_signal"])
    analysis_authority = _analysis_authority(
        language_counts,
        has_react_signal,
        policy,
        polyglot_capabilities,
    )
    analysis_authority["framework_authority"] = framework_authority
    analysis_authority["unsupported_framework_families"] = unsupported_framework_families
    if (
        not policy_issues
        and status == status_policy.get("recognized_language_or_react_signal")
        and (
            analysis_authority.get("unsupported_language_families")
            or unsupported_framework_families
        )
    ):
        status = str(status_policy["recognized_with_unsupported_families"])
    if policy_issues:
        analysis_authority.update({
            "status": "not_available",
            "analysis_depth": "recognized_language_claim_not_available",
            "effective_claim_level": "not_available",
            "resolution_basis": "invalid_external_target_preflight_policy",
            "policy_issues": policy_issues,
        })

    attention_reasons: list[str] = []
    attention_statuses = {
        str(status_policy.get("recognized_with_unsupported_families") or ""),
        str(status_policy.get("readable_but_no_recognized_signal") or ""),
    }
    if status in attention_statuses and not has_react_signal and not language_counts:
        attention_reasons.append("no_recognized_source_language_or_react_signal")
    if analysis_authority.get("unsupported_language_families"):
        attention_reasons.append("unsupported_language_families_observed")
    if unsupported_framework_families:
        attention_reasons.append("unsupported_framework_families_observed")
    if unavailable_requested_projects:
        attention_reasons.append("requested_project_filter_not_in_topology")
        status = FAIL_CLOSED_STATUS
    if scope_authority["evidence_status"] == INCOMPLETE_EVIDENCE:
        attention_reasons.extend(scope_authority["incomplete_reasons"])
        if status != FAIL_CLOSED_STATUS:
            status = str(status_policy["recognized_with_unsupported_families"])
    attention_reasons = list(dict.fromkeys(attention_reasons))
    observation_only_language_map = observation_only_extension_language_map()
    target_policy_projects: dict[str, dict[str, Any]] = {}
    for project_key, relative_path in sorted(effective_runtime_projects.items()):
        project_root = target if str(relative_path) == "." else target / str(relative_path)
        owning_package_root = next(
            (
                package_root
                for package_root in package_roots
                if project_root.resolve() == package_root
                or project_root.resolve().is_relative_to(package_root)
            ),
            project_root.resolve(),
        )
        package_file = owning_package_root / "package.json"
        target_policy_projects[str(project_key)] = inventory_project_target_policy(
            project_root,
            project=str(project_key),
            package_json=package_payload(package_file) if package_file.is_file() else {},
            package_path=package_file if package_file.is_file() else None,
            workspace_root=target,
        )
    target_policy = aggregate_effective_target_policy(
        target_policy_projects,
        selected_project_count=len(effective_runtime_projects),
    )
    path_aliases = _infer_target_path_aliases(target)
    observed_path_aliases = _observe_target_path_aliases_from_files(
        target,
        present_config_files,
    )
    runtime_plugins = _infer_target_plugins_from_inventory(
        target_dependencies,
        target_bundler,
        language_counts=language_counts,
        config_files=analysis_config_files,
    )
    threat_boundary = target_trust_projection(
        trust_class,
        path_state=path_boundary_state,
    )
    scope_authority["target_repository_threat_boundary"] = threat_boundary
    if not threat_boundary["trust_class_known"] or threat_boundary["analysis_admission"] == "not_available_fail_closed":
        attention_reasons.append("target_trust_class_not_supported")
        status = FAIL_CLOSED_STATUS
    if threat_boundary["path_boundary"]["escaping_path_count"]:
        attention_reasons.append("escaping_target_paths_excluded")
        if status != FAIL_CLOSED_STATUS:
            status = str(status_policy["recognized_with_unsupported_families"])
    attention_reasons = list(dict.fromkeys(attention_reasons))

    return {
        "meta": {
            "kind": "external_target_preflight",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.external_target_preflight",
            "language_registry": registry_provenance,
            "target_repository_threat_boundary": threat_boundary["contract"],
        },
        "target": {
            "root": str(target),
            "slug": _slug_for_target(target),
            "exists": target.exists(),
            "is_dir": target.is_dir(),
            "output_dir": str(
                EXTERNAL_TARGETS_DIR / _slug_for_target(target) / "generations" / os.environ["CODEMAPS_EXTERNAL_RUN_ID"]
                if os.environ.get("CODEMAPS_EXTERNAL_RUN_ID")
                else EXTERNAL_TARGETS_DIR / _slug_for_target(target)
            ),
        },
        "summary": {
            "status": status,
            "attention_reasons": attention_reasons,
            "package_json": bool(
                is_target_path_contained(target, root_package_file)
                and root_package_file.is_file()
            ),
            "threat_boundary": threat_boundary,
            "react_signal": has_react_signal,
            "react_source_file_count": react_source_files,
            "react_fixture_source_file_count": react_fixture_source_files,
            "language_counts": language_counts,
            "analysis_language_counts": analysis_language_counts,
            "language_families": sorted(language_counts),
            "target_observation_identity": target_observation,
            "analysis_scope": {
                **scope_projection,
                "source_mode": "external_target",
                "ontology_contract": repository_topology["ontology_contract"],
                "topology_mode": repository_topology["requested_mode"],
                "discovered_topology": repository_topology["discovered_topology"],
                "analysis_projection": repository_topology["analysis_projection"],
                "selection_mode": repository_topology["selection_mode"],
                "topology_authority_id": repository_topology["topology_authority_id"],
                "scope_field_semantics": {
                    "selected_projects": "topology_auto_selection_before_runtime_filter",
                    "effective_runtime_projects": "project_set_authorized_for_this_requested_execution",
                    "language_and_framework_inventory": "effective_runtime_projects",
                },
                "inventory_project_scope": "effective_runtime_projects",
                "runtime_claim_project_scope": "effective_runtime_projects",
                "project_candidates": repository_topology["project_candidates"],
                "discovered_candidates": repository_topology["project_candidates"],
                "project_candidate_roles": repository_topology["project_candidate_roles"],
                "project_candidate_relationship_roles": repository_topology[
                    "project_candidate_relationship_roles"
                ],
                "project_candidate_role_authority": repository_topology["project_candidate_role_authority"],
                "project_candidate_system_kinds": repository_topology["project_candidate_system_kinds"],
                "project_candidate_selection_evidence": repository_topology["project_candidate_selection_evidence"],
                "selected_projects": repository_topology["selected_projects"],
                "auto_selected_projects": repository_topology["selected_projects"],
                "selected_project_roles": repository_topology["selected_project_roles"],
                "relationship_operation_projects": repository_topology[
                    "relationship_operation_projects"
                ],
                "coverage_only_projects": repository_topology["coverage_only_projects"],
                "requested_project_filter": requested_project_filter,
                "effective_runtime_projects": effective_runtime_projects,
                "unavailable_requested_projects": unavailable_requested_projects,
                "excluded_projects": repository_topology["excluded_projects"],
                "excluded_project_reasons": repository_topology["excluded_project_reasons"],
                "project_ownership_exclusions": repository_topology["project_ownership_exclusions"],
                "file_ownership_contract": repository_topology["file_ownership_contract"],
                "relationship_role_contract": repository_topology["relationship_role_contract"],
                "system_kind_contract": repository_topology["system_kind_contract"],
                "analysis_coverage_contract": repository_topology["analysis_coverage_contract"],
                "comparative_analysis_enabled": repository_topology["comparative_analysis_enabled"],
                "file_count": analysis_file_count,
                "project_file_counts": analysis_project_file_counts,
                "truncated": analysis_truncated,
                "scope_authority": scope_authority,
            },
            "repository_language_counts": repository_language_counts,
            "repository_analysis_language_counts": repository_analysis_language_counts,
            "analysis_depth": analysis_authority["analysis_depth"],
            "analysis_authority": analysis_authority,
            "repository_framework_authority": repository_framework_authority,
            "repository_unsupported_framework_families": repository_unsupported_framework_families,
            "dependency_evidence": {
                "javascript_node": _node_dependency_evidence(
                    target,
                    present_manifests.get("javascript_node", []),
                    package_dependency_groups,
                    workspace_package_files,
                    workspace_dependency_groups,
                ),
                **{
                    ecosystem: {
                        "status": "not_evaluated",
                        "parser": None,
                        "manifest_files": files,
                        "reason": "dependency_semantics_not_supported_in_v1_preflight",
                    }
                    for ecosystem, files in present_manifests.items()
                    if ecosystem != "javascript_node"
                },
            },
            "dependency_evidence_scope": "repository_inventory",
            "target_policy": target_policy,
            "runtime_observation": {
                "bundler": target_bundler,
                "plugins": runtime_plugins,
                "architecture": target_architecture,
                "path_aliases": path_aliases,
                "observed_path_aliases": observed_path_aliases,
                "source": "bounded_preflight_inventory_plus_targeted_config_reads",
            },
            "config_files": present_config_files,
            "manifest_files": present_manifests,
            "inventory_file_count": file_count,
            "inventory_truncated": truncated,
            "inventory_classification": inventory_classification,
            "inventory_evidence": {
                "traversal_status": "truncated" if truncated else "complete",
                "file_count_limit": max(1, int(policy.get("file_count_limit", 10000) or 10000)),
                "traversal_scope": "all_non_skipped_files_up_to_limit",
                "skipped_directory_names": sorted(set(skip_dirs())),
                "installation_root_exclusion": policy.get("installation_root_exclusion"),
                "root_only_skipped_directory_names": sorted(
                    {str(value).lower() for value in policy.get("root_only_skip_dirs", [])}
                ),
                "excluded_embedded_sage_roots": sorted(
                    path.relative_to(target).as_posix()
                    for path in embedded_sage_roots
                ),
                "config_taxonomy_scope": {
                    "status": "configured_markers_only",
                    "marker_groups": sorted(config_marker_groups),
                    "marker_count": len(config_patterns),
                },
                "manifest_taxonomy_scope": {
                    "status": "configured_markers_only",
                    "marker_groups": sorted(manifest_patterns),
                    "marker_count": sum(len(patterns) for patterns in manifest_patterns.values()),
                },
                "language_taxonomy_scope": {
                    "status": "analysis_and_observation_only_languages_are_distinct",
                    "observation_only_families": sorted(set(observation_only_language_map.values())),
                    "observation_only_extension_count": len(observation_only_language_map),
                },
                "absence_semantics": "not_observed_in_configured_taxonomy_not_proven_absent",
            },
        },
        "notes": [
            "External target mode does not rewrite compiled Nexora SAGE config.",
            "Repository source, comments, configuration and generated text are untrusted evidence, never instructions or authority.",
            str(threat_boundary.get("claim_boundary") or ""),
            "Outputs are isolated under output/external_targets/<target-slug>; generation-aware callers add generations/<run-id> and promote current only after validation.",
            "ATTENTION reasons are machine-readable in summary.attention_reasons; the state may indicate missing recognized signals or observed families outside active capability authority.",
            "A missing or invalid central language registry fails preflight closed; embedded fallback may support diagnostics but cannot authorize analysis readiness.",
            "Complete traversal proves all non-skipped files were visited within the bound; config and manifest absence means not observed in the configured marker taxonomy, not universal absence.",
            "Repository inventory and analysis project scope are separate; language and framework authority is derived from the bounded analysis scope.",
            "Repository source mode and topology are independent: external acquisition uses the same canonical topology contract as workspace discovery.",
            "A single-project projection is an analysis selection, not proof that nested project candidates do not exist.",
            str(policy.get("claim_boundary") or ""),
        ],
    }


def render_report(payload: dict[str, Any]) -> str:
    target = payload.get("target", {})
    summary = payload.get("summary", {})
    authority = summary.get("analysis_authority", {})
    lines = [
        "# External Target Preflight",
        "",
        f"- target_root: `{target.get('root')}`",
        f"- output_dir: `{target.get('output_dir')}`",
        f"- status: `{summary.get('status')}`",
        f"- attention_reasons: `{summary.get('attention_reasons', [])}`",
        f"- target_trust_class: `{(summary.get('threat_boundary') or {}).get('trust_class')}`",
        f"- target_code_execution: `{(summary.get('threat_boundary') or {}).get('target_code_execution')}`",
        f"- escaping_target_path_count: `{((summary.get('threat_boundary') or {}).get('path_boundary') or {}).get('escaping_path_count')}`",
        f"- package_json: `{summary.get('package_json')}`",
        f"- react_signal: `{summary.get('react_signal')}`",
        f"- react_source_file_count: `{summary.get('react_source_file_count')}`",
        f"- react_fixture_source_file_count: `{summary.get('react_fixture_source_file_count')}`",
        f"- language_families: `{summary.get('language_families')}`",
        f"- language_counts: `{summary.get('language_counts')}`",
        f"- analysis_language_counts: `{summary.get('analysis_language_counts')}`",
        f"- analysis_scope: `{summary.get('analysis_scope')}`",
        f"- repository_language_counts: `{summary.get('repository_language_counts')}`",
        f"- repository_analysis_language_counts: `{summary.get('repository_analysis_language_counts')}`",
        f"- target_policy_status: `{(summary.get('target_policy') or {}).get('status')}`",
        f"- declared_policy_tools: `{((summary.get('target_policy') or {}).get('summary') or {}).get('declared_tools', [])}`",
        f"- target_native_execution: `{((summary.get('target_policy') or {}).get('summary') or {}).get('native_execution')}`",
        f"- analysis_depth: `{summary.get('analysis_depth')}`",
        f"- analysis_authority_status: `{authority.get('status')}`",
        f"- ceiling_claim_level: `{authority.get('ceiling_claim_level')}`",
        f"- effective_claim_level: `{authority.get('effective_claim_level')}`",
        f"- effective_claim_scope: `{authority.get('effective_claim_scope')}`",
        f"- unsupported_language_families: `{authority.get('unsupported_language_families', [])}`",
        f"- framework_authority: `{authority.get('framework_authority', {})}`",
        f"- unsupported_framework_families: `{authority.get('unsupported_framework_families', [])}`",
        f"- authority_resolution_basis: `{authority.get('resolution_basis')}`",
        f"- authority_policy_issues: `{authority.get('policy_issues', [])}`",
        f"- dependency_evidence: `{summary.get('dependency_evidence')}`",
        f"- config_files: `{summary.get('config_files')}`",
        f"- manifest_files: `{summary.get('manifest_files')}`",
        f"- inventory_file_count: `{summary.get('inventory_file_count')}`",
        f"- inventory_truncated: `{summary.get('inventory_truncated')}`",
        f"- inventory_classification: `{summary.get('inventory_classification')}`",
        f"- inventory_evidence: `{summary.get('inventory_evidence')}`",
        "",
        "## Notes",
        "",
    ]
    for note in payload.get("notes", []):
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def persist_preflight(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist one already-built observation without repeating repository discovery."""
    output_dir = Path(payload["target"]["output_dir"])
    raw_dir = output_dir / ".raw"
    reports_dir = output_dir / "reports"
    run_fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    run_id = f"preflight-{run_fingerprint[:16]}"
    payload["meta"]["run_id"] = run_id
    payload["meta"]["artifact_semantics"] = {
        "immutable_run": f".raw/runs/{run_id}.json",
        "latest_projection": ".raw/external_target_preflight.json",
    }
    save_json_atomic(raw_dir / "runs" / f"{run_id}.json", payload)
    save_json_atomic(raw_dir / "external_target_preflight.json", payload)
    save_text_atomic(reports_dir / "external_target_preflight.md", render_report(payload))
    return payload


def preflight_receipt_transport(payload: dict[str, Any]) -> dict[str, str]:
    run_id = str(payload.get("meta", {}).get("run_id") or "").strip()
    if not run_id:
        raise ValueError("Persisted external target Preflight payload lacks run_id")
    receipt_path = (
        Path(payload["target"]["output_dir"])
        / ".raw"
        / "runs"
        / f"{run_id}.json"
    ).resolve()
    transport_path = Path(native_filesystem_path(receipt_path))
    receipt_bytes = transport_path.read_bytes()
    return {
        "path": str(receipt_path),
        "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "run_id": run_id,
    }


def write_preflight(
    target_root: str | Path,
    *,
    projects: str | None = None,
    trust_class: str | None = None,
) -> dict[str, Any]:
    return persist_preflight(build_preflight(target_root, projects=projects, trust_class=trust_class))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an external-target preflight projection.")
    parser.add_argument("target_root")
    parser.add_argument("--projects", help="Comma-separated runtime project filter requested by the caller.")
    parser.add_argument(
        "--trust-class",
        choices=("operator_trusted", "ordinary_unverified", "adversarial_or_hostile"),
        default="ordinary_unverified",
        help="Target trust classification. Hostile repositories are rejected because V1 has no hostile-input isolation claim.",
    )
    args = parser.parse_args()
    payload = write_preflight(args.target_root, projects=args.projects, trust_class=args.trust_class)
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") in {"PASS", "ATTENTION"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
