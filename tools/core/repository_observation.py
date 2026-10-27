"""Acquisition-neutral static project facts; permissions and runtime proof live elsewhere."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from tools.core.repository_topology import classify_project_system_kinds, scope_authority_id
from tools.core.target_inventory import _language_inventory_policy, repository_and_selected_project_inventory
from tools.core.target_repository_trust import is_target_path_contained
from tools.core.unmanaged_atomic_io import native_filesystem_path

CONTRACT = "repository_project_observation_v1"


def _identity(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def observation_policy_identity(policy, capabilities, kind_policy, config_patterns, manifest_patterns):
    return _identity({"inventory": policy, "capabilities": capabilities, "system_kind": kind_policy,
                      "language_registry": _language_inventory_policy(),
                      "configuration_markers": sorted(config_patterns), "manifest_markers": manifest_patterns})


def _ownership_identity(topology):
    return _identity({"topology": scope_authority_id(topology, None),
                      "contract": topology.get("file_ownership_contract"),
                      "exclusions": topology.get("project_ownership_exclusions", {})})


@dataclass(frozen=True)
class ProjectObservation:
    """A producer result for one invocation, not an admission token stored in target JSON."""
    _serialized: str
    run_id: str

    def payload(self) -> dict[str, Any]:
        return json.loads(self._serialized)


def observe_repository_projects(
    target: Path, topology: dict[str, Any], policy: dict[str, Any],
    capabilities: dict[str, Any], kind_policy: dict[str, Any],
    config_patterns: list[str], manifest_patterns: dict[str, list[str]], *,
    projects: dict[str, Any] | None = None, excluded_roots: set[Path] | None = None,
    package_react_ownership: dict[Path, bool] | None = None,
    package_payload: Callable[[Path], Any] | None = None,
    path_boundary_state: dict[str, Any] | None = None, run_id: str = "",
) -> ProjectObservation:
    """Reuse the existing one-walk inventory and doctrine classifier exactly once."""
    target = Path(target).resolve()
    selected = dict(topology["selected_projects"] if projects is None else projects)
    manifests: dict[Path, Any] = {}

    def manifest(path):
        if not is_target_path_contained(target, path, state=path_boundary_state):
            return {}
        path = path.resolve()
        if path not in manifests:
            if package_payload is not None:
                manifests[path] = package_payload(path)
            else:
                from tools.core.json_syntax import loads_json_strict
                native = Path(native_filesystem_path(path))
                try:
                    manifests[path] = loads_json_strict(native.read_text(encoding="utf-8-sig")) if native.is_file() else {}
                except (OSError, UnicodeError, ValueError):
                    manifests[path] = {}
        return manifests[path] if isinstance(manifests[path], dict) else {}

    project_manifests = {
        key: manifest(target / str(relative) / "package.json") for key, relative in selected.items()
    }
    if package_react_ownership is None:
        react = set((policy.get("framework_source_evidence", {}).get("react") or {}).get("package_names") or [])
        package_react_ownership = {}
        for directory in {target, *(target / str(path) for path in selected.values())}:
            package = manifest(directory / "package.json")
            sections = policy.get("node_dependency_sections", {}).values()
            names = {name for section in sections for name in (
                package.get(section, {}) if isinstance(package.get(section), dict) else {})}
            package_react_ownership[directory.resolve()] = bool(names & react)
    observation, classification = {}, {}
    if target.is_dir():
        repository, effective = repository_and_selected_project_inventory(
            target, topology, policy, config_patterns, manifest_patterns, package_react_ownership,
            excluded_roots=excluded_roots, projects=selected, observation_state=observation,
            classification_projection=classification, path_boundary_state=path_boundary_state,
        )
    else:
        repository, effective = (0, False, {}, {}, 0, 0, [], {}), (0, False, {}, {}, 0, 0, [], {}, {}, {})
        from tools.core.target_inventory import TARGET_OBSERVATION_IDENTITY_ALGORITHM
        observation = {"algorithm": TARGET_OBSERVATION_IDENTITY_ALGORITHM,
                       "status": "incomplete", "error": "invalid_target", "fingerprint": None,
                       "entry_count": 0, "decision_file_count": 0, "observation_contract_sha256": None}
    payload = {
        "contract": CONTRACT, "target_root": target.as_posix(),
        "topology_authority_id": scope_authority_id(topology, None),
        "ownership_identity": _ownership_identity(topology),
        "captured_projects": selected,
        "policy_identity": observation_policy_identity(policy, capabilities, kind_policy, config_patterns, manifest_patterns),
        "repository_inventory": repository, "selected_inventory": effective,
        "project_system_kinds": classify_project_system_kinds(effective[9], project_manifests, kind_policy),
        "system_kind_contract": str(kind_policy.get("contract") or "technical_system_kind_unresolved_v1"),
        "target_observation": observation, "inventory_classification": classification,
    }
    payload["identity"] = _identity(payload)
    return ProjectObservation(json.dumps(payload, ensure_ascii=False), str(run_id))


def current_observation_inputs():
    """Late-bound SSoT inputs; never scan a target during module/config import."""
    from tools.core.config import CONFIG_DIR, DOCTRINE
    from tools.core.json_io import load_json_object_strict_cached
    from tools.core.language_registry import config_file_marker_map, manifest_file_marker_map
    policy = load_json_object_strict_cached(CONFIG_DIR / "external_target_preflight_policy.json")
    capabilities = load_json_object_strict_cached(CONFIG_DIR / "polyglot_capabilities.json")
    kinds = DOCTRINE.get("discovery_project_system_kind_policy", {})
    patterns = sorted({name for values in config_file_marker_map().values() for name in values})
    return policy, capabilities, kinds, patterns, manifest_file_marker_map()


def observe_current_repository(target, topology, *, projects=None, run_id=""):
    from tools.core.config import CODE_MAPS_DIR
    from tools.core.installation_identity import runtime_installation_excluded_roots
    return observe_repository_projects(
        target, topology, *current_observation_inputs(), projects=projects, run_id=run_id,
        excluded_roots=runtime_installation_excluded_roots(Path(target), CODE_MAPS_DIR),
    )


def validate_run_observation(observation, *, target, topology, projects, run_id):
    """Admit only a current producer object, not a serialized proposal or old run."""
    if not isinstance(observation, ProjectObservation) or not run_id or observation.run_id != run_id:
        return None, "project_observation_not_current_for_run"
    try:
        payload = observation.payload()
    except (ValueError, TypeError):
        return None, "project_observation_invalid_payload"
    if not isinstance(payload, dict):
        return None, "project_observation_invalid_payload"
    claimed = payload.pop("identity", None)
    if claimed != _identity(payload):
        return None, "project_observation_identity_mismatch"
    payload["identity"] = claimed
    if payload.get("contract") != CONTRACT or payload.get("target_root") != Path(target).resolve().as_posix():
        return None, "project_observation_target_mismatch"
    if (payload.get("topology_authority_id") != scope_authority_id(topology, None)
            or payload.get("ownership_identity") != _ownership_identity(topology)):
        return None, "project_observation_topology_mismatch"
    if payload.get("policy_identity") != observation_policy_identity(*current_observation_inputs()):
        return None, "project_observation_policy_mismatch"
    if payload.get("target_observation", {}).get("status") != "complete":
        return None, "project_observation_inventory_unavailable"
    captured = payload.get("captured_projects", {})
    if any(captured.get(key) != value for key, value in projects.items()):
        return None, "project_observation_coverage_mismatch"
    return payload, None
