from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.installation_identity import (
    EMBEDDED_TARGET_MODE,
    installation_target_mode,
)
from tools.core.json_io import load_json_object_strict
from tools.core.python_runtime_env import utf8_subprocess_env


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
CONTRACT_PATH = CONFIG_DIR / "installation_preflight_contract.json"
RAW_OUTPUT_PATH = RAW_DIR / "installation_preflight.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "installation_preflight.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_installation_preflight_contract() -> dict[str, Any]:
    return load_json_object_strict(CONTRACT_PATH, label="Installation preflight contract")


def installation_feature_dependencies(
    contract: dict[str, Any] | None = None,
) -> dict[str, tuple[str, str, str | None]]:
    payload = contract or load_installation_preflight_contract()
    dependencies: dict[str, tuple[str, str, str | None]] = {}
    for feature, spec in payload.get("python_features", {}).items():
        if not isinstance(spec, dict) or not spec.get("required_by_default_profile"):
            continue
        dependencies[str(feature)] = (
            str(spec.get("module") or ""),
            str(spec.get("package") or ""),
            str(spec["attribute"]) if spec.get("attribute") else None,
        )
    return dependencies


def _host_access_observation(path: Path) -> dict[str, Any]:
    try:
        try:
            metadata = path.stat()
        except FileNotFoundError:
            return {"status": "not_present"}
        if stat.S_ISDIR(metadata.st_mode):
            with os.scandir(path) as entries:
                next(entries, None)
            return {"status": "traversable"}
        with path.open("rb") as handle:
            handle.read(1)
        return {"status": "readable"}
    except OSError as exc:
        return {
            "status": "blocked",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }


def _declared_target_tools(target_profile: dict[str, Any]) -> list[str]:
    target_policy = target_profile.get("target_policy")
    if not isinstance(target_policy, dict):
        return []
    summary = target_policy.get("summary")
    if not isinstance(summary, dict):
        return []
    return sorted({
        str(value)
        for value in summary.get("declared_tools", [])
        if str(value).strip()
    })


def build_embedded_host_footprint(
    target_root: str | Path,
    installation_root: str | Path,
    *,
    contract: dict[str, Any],
    target_profile: dict[str, Any],
) -> dict[str, Any]:
    """Project one non-mutating host-tool exclusion envelope for embedded installs."""

    target = Path(target_root).resolve()
    installation = Path(installation_root).resolve()
    footprint_contract = contract.get("embedded_host_footprint", {})
    mode = installation_target_mode(target, installation)
    applicability = (
        footprint_contract.get("applicability_by_installation_mode", {}).get(mode)
        or "unknown_installation_mode"
    )
    declared_tools = _declared_target_tools(target_profile)
    base = {
        "contract": footprint_contract.get("contract"),
        "installation_mode": mode,
        "applicability": applicability,
        "installation_root": str(installation),
        "target_root": str(target),
        "embedded_root": None,
        "single_root_exclusion": bool(footprint_contract.get("single_root_exclusion")),
        "declared_target_tools": declared_tools,
        "detected_host_tools": [],
        "declared_surfaces": [],
        "observed_surfaces": [],
        "host_tool_guidance": [],
        "traversal": {"status": "not_applicable", "blocked_paths": []},
        "target_configuration_mutation": {
            "allowed": bool(footprint_contract.get("target_configuration_mutation_allowed")),
            "performed": False,
        },
        "permission_mutation": {
            "allowed": bool(
                footprint_contract.get("traversal_probe", {}).get(
                    "permission_mutation_allowed"
                )
            ),
            "performed": False,
        },
        "claim_boundary": footprint_contract.get("claim_boundary"),
    }
    if mode != EMBEDDED_TARGET_MODE:
        return base

    embedded_root = installation.relative_to(target).as_posix()
    templates = footprint_contract.get("pattern_templates", {})
    exclusion = {
        str(name): str(template).replace("{embedded_root}", embedded_root)
        for name, template in templates.items()
        if str(name).strip() and str(template).strip()
    }
    declared_surfaces: list[dict[str, Any]] = []
    observed_surfaces: list[dict[str, Any]] = []
    blocked_paths: list[str] = []
    seen_paths: set[str] = set()
    for surface in footprint_contract.get("runtime_surfaces", []):
        if not isinstance(surface, dict):
            continue
        declared_surfaces.append({
            "id": str(surface.get("id") or "unknown"),
            "kind": str(surface.get("kind") or "unknown"),
            "relative_paths": [str(value) for value in surface.get("relative_paths", [])],
            "recursive_names": [str(value) for value in surface.get("recursive_names", [])],
        })
        for relative in surface.get("relative_paths", []):
            relative_path = str(relative or ".")
            if relative_path in seen_paths:
                continue
            seen_paths.add(relative_path)
            candidate = installation if relative_path == "." else installation / relative_path
            access = _host_access_observation(candidate)
            row = {
                "surface": str(surface.get("id") or "unknown"),
                "kind": str(surface.get("kind") or "unknown"),
                "path": relative_path,
                "exists": access.get("status") != "not_present",
                "host_access": access,
            }
            observed_surfaces.append(row)
            if access.get("status") == "blocked":
                blocked_paths.append(relative_path)

    host_tools = footprint_contract.get("host_tools", {})
    detected_tools = sorted(set(declared_tools) & set(host_tools))
    guidance: list[dict[str, Any]] = []
    for tool_id in detected_tools:
        tool = host_tools[tool_id]
        guidance.append({
            "id": tool_id,
            "surface": tool.get("surface"),
            "detection": "target_policy_profile",
            "status": "operator_review_required",
            "recommended_exclusion": exclusion,
            "configuration_surfaces": list(tool.get("configuration_surfaces", [])),
            "target_mutation_performed": False,
        })
    for surface_id, surface in footprint_contract.get("baseline_surfaces", {}).items():
        if not isinstance(surface, dict):
            continue
        guidance.append({
            "id": str(surface_id),
            "surface": surface.get("surface"),
            "detection": "embedded_installation_baseline",
            "status": "operator_review_required",
            "recommended_exclusion": exclusion,
            "configuration_surfaces": list(surface.get("configuration_surfaces", [])),
            "target_mutation_performed": False,
        })

    base.update({
        "embedded_root": embedded_root,
        "root_exclusion": exclusion,
        "detected_host_tools": detected_tools,
        "declared_surfaces": declared_surfaces,
        "observed_surfaces": observed_surfaces,
        "host_tool_guidance": guidance,
        "traversal": {
            "status": "blocked" if blocked_paths else "traversable",
            "blocked_paths": sorted(blocked_paths),
        },
    })
    return base


def _module_target_available(module_name: str, attribute_name: str | None = None) -> bool:
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return False
    return not attribute_name or hasattr(module, attribute_name)


def _command_observation(command: str, version_args: list[str]) -> dict[str, Any]:
    executable = shutil.which(command)
    if not executable:
        return {"available": False, "command": command, "path": None, "version": None}
    try:
        result = subprocess.run(
            [executable, *version_args],
            check=False,
            capture_output=True,
            env=utf8_subprocess_env(),
            text=True,
            timeout=5,
        )
        version = (result.stdout or result.stderr or "").strip().splitlines()[0]
        return {
            "available": result.returncode == 0,
            "command": command,
            "path": executable,
            "version": version or None,
            "returncode": result.returncode,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "available": False,
            "command": command,
            "path": executable,
            "version": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _major_version(version: Any) -> int | None:
    match = re.search(r"(?:^|\D)(\d+)(?:\.|$)", str(version or ""))
    return int(match.group(1)) if match else None


def _python_contract(root: Path = CODE_MAPS_DIR) -> tuple[str, list[str]]:
    payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirement = str(payload.get("project", {}).get("requires-python") or "")
    tested = (
        payload.get("tool", {})
        .get("nexora_sage", {})
        .get("distribution", {})
        .get("tested_python_versions", [])
    )
    return requirement, [str(value) for value in tested]


def _minimum_python_version(requirement: str) -> tuple[int, int] | None:
    match = re.search(r">=\s*(\d+)\.(\d+)", requirement)
    return (int(match.group(1)), int(match.group(2))) if match else None


def observe_machine(
    contract: dict[str, Any] | None = None,
    *,
    feature_availability: dict[str, bool] | None = None,
) -> dict[str, Any]:
    payload = contract or load_installation_preflight_contract()
    requirement, tested_versions = _python_contract()
    current_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    minimum = _minimum_python_version(requirement)
    python_supported = minimum is not None and sys.version_info[:2] >= minimum
    features = {}
    injected = feature_availability or {}
    for feature, (module_name, package_name, attribute_name) in installation_feature_dependencies(payload).items():
        available = (
            bool(injected[feature])
            if feature in injected
            else _module_target_available(module_name, attribute_name)
        )
        features[feature] = {
            "available": available,
            "module": module_name,
            "attribute": attribute_name,
            "package": package_name,
            "required_by_profile": True,
        }
    return {
        "platform": platform.platform(),
        "python": {
            "available": True,
            "path": sys.executable,
            "version": platform.python_version(),
            "minor": current_minor,
            "requirement": requirement,
            "requirement_satisfied": python_supported,
            "release_tested": current_minor in tested_versions,
            "tested_versions": tested_versions,
        },
        "pip": {
            "available": importlib.util.find_spec("pip") is not None,
            "path": f"{sys.executable} -m pip",
        },
        "node": _command_observation("node", ["--version"]),
        "npm": _command_observation("npm", ["--version"]),
        "git": _command_observation("git", ["--version"]),
        "python_features": features,
    }


def _target_profile(target_preflight: dict[str, Any]) -> dict[str, Any]:
    summary = target_preflight.get("summary", {})
    target = target_preflight.get("target", {})
    target_policy = summary.get("target_policy", {})
    target_policy_summary = (
        target_policy.get("summary", {}) if isinstance(target_policy, dict) else {}
    )
    language_counts = {
        str(key): int(value or 0)
        for key, value in (summary.get("language_counts") or {}).items()
    }
    node_evidence = (summary.get("dependency_evidence") or {}).get("javascript_node", {})
    return {
        "root": target.get("root"),
        "exists": bool(target.get("exists")),
        "is_dir": bool(target.get("is_dir")),
        "preflight_status": summary.get("status"),
        "attention_reasons": list(summary.get("attention_reasons") or []),
        "language_counts": language_counts,
        "language_families": sorted(language_counts),
        "react_signal": bool(summary.get("react_signal")),
        "node_manifest_status": node_evidence.get("status"),
        "analysis_authority": summary.get("analysis_authority", {}),
        "target_policy": {
            "summary": {
                "declared_tools": list(target_policy_summary.get("declared_tools", []))
            }
        },
        "source": "tools.external_target_preflight.build_preflight",
    }


def _resolved_command(template: list[str], *, npm: str | None = None) -> list[str]:
    values = {
        "{python}": sys.executable,
        "{sage_root}": str(CODE_MAPS_DIR),
        "{npm}": str(npm or "npm"),
    }
    return [values.get(str(value), str(value)) for value in template]


def _dependency_content_identity(paths: list[Path], *, root: Path) -> str | None:
    digest = hashlib.sha256()
    for path in paths:
        if not path.is_file():
            return None
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def node_ast_cache_state(
    node_contract: dict[str, Any],
    *,
    root: Path = CODE_MAPS_DIR,
) -> dict[str, Any]:
    """Verify the bounded TypeScript runtime against its manifest and lock identity."""

    manifest = root / str(node_contract.get("package_manifest") or "")
    lockfile = manifest.parent / "package-lock.json"
    marker = root / str(node_contract.get("bundled_typescript_marker") or "")
    content_identity = _dependency_content_identity([manifest, lockfile], root=root)
    expected_version = locked_version = installed_version = None
    errors: list[str] = []
    try:
        manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
        expected_version = manifest_payload.get("dependencies", {}).get("typescript")
    except (OSError, json.JSONDecodeError, AttributeError):
        errors.append("package_manifest_unreadable")
    try:
        lock_payload = json.loads(lockfile.read_text(encoding="utf-8"))
        locked_version = lock_payload.get("packages", {}).get(
            "node_modules/typescript", {}
        ).get("version")
    except (OSError, json.JSONDecodeError, AttributeError):
        errors.append("package_lock_unreadable")
    try:
        marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        installed_version = marker_payload.get("version")
    except FileNotFoundError:
        errors.append("installed_typescript_missing")
    except (OSError, json.JSONDecodeError, AttributeError):
        errors.append("installed_typescript_marker_unreadable")
    if expected_version and locked_version != expected_version:
        errors.append("manifest_lock_version_mismatch")
    if expected_version and installed_version and installed_version != expected_version:
        errors.append("installed_version_mismatch")
    verified = bool(content_identity and expected_version and not errors)
    return {
        "status": "VERIFIED" if verified else "REINSTALL_REQUIRED",
        "content_identity": content_identity,
        "expected_typescript_version": expected_version,
        "locked_typescript_version": locked_version,
        "installed_typescript_version": installed_version,
        "errors": sorted(set(errors)),
        "claim_boundary": (
            "Manifest, lockfile and installed TypeScript version are bound. "
            "This does not hash every installed runtime file."
        ),
    }


def _dependency_action_metadata(
    contract: dict[str, Any],
    action_id: str,
) -> dict[str, Any]:
    acquisition = contract.get("dependency_acquisition", {})
    profiles = acquisition.get("action_profiles", {}) if isinstance(acquisition, dict) else {}
    profile = profiles.get(action_id) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise ValueError(f"Installation contract has no dependency action profile: {action_id}")
    authority = str(profile.get("dependency_authority") or "")
    authorities = acquisition.get("authorities", {})
    authority_contract = authorities.get(authority) if isinstance(authorities, dict) else None
    if not isinstance(authority_contract, dict):
        raise ValueError(f"Unknown dependency authority for {action_id}: {authority}")
    if authority != "sage_runtime" or not bool(authority_contract.get("automatic_on_init")):
        raise ValueError(f"Dependency action is not authorized for automatic init: {action_id}")
    return {
        "dependency_authority": authority,
        "operation_class": str(profile.get("operation_class") or ""),
        "telemetry_identifier": str(profile.get("telemetry_identifier") or ""),
        "capability_source": str(profile.get("capability_source") or ""),
        "progress_relative_paths": [
            str(value) for value in profile.get("progress_relative_paths", [])
        ],
        "stall_authority_sources": [
            str(value) for value in profile.get("stall_authority_sources", [])
        ],
    }


def build_installation_plan(
    target_root: str | Path,
    *,
    target_preflight: dict[str, Any] | None = None,
    target_profile: dict[str, Any] | None = None,
    machine: dict[str, Any] | None = None,
    feature_availability: dict[str, bool] | None = None,
    bundled_typescript_available: bool | None = None,
    dependency_install_enabled: bool = True,
    installation_root: str | Path | None = None,
) -> dict[str, Any]:
    contract = load_installation_preflight_contract()
    if target_profile is not None:
        target = dict(target_profile)
    else:
        if target_preflight is None:
            from tools.external_target_preflight import build_preflight

            target_preflight = build_preflight(target_root)
        target = _target_profile(target_preflight)
    machine_state = machine or observe_machine(contract, feature_availability=feature_availability)
    node_contract = contract.get("node", {})
    status_policy = contract.get("status_policy", {})
    blockers: list[dict[str, Any]] = []
    attention: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    installation_footprint = build_embedded_host_footprint(
        target_root,
        installation_root or CODE_MAPS_DIR,
        contract=contract,
        target_profile=target,
    )

    if installation_footprint.get("installation_mode") == EMBEDDED_TARGET_MODE:
        attention.append({
            "id": "embedded_sage_host_tool_isolation_guidance",
            "reason": (
                "the SAGE installation is inside the target repository; apply the emitted "
                "single-root guidance through target-owned host-tool configuration"
            ),
            "embedded_root": installation_footprint.get("embedded_root"),
            "detected_host_tools": installation_footprint.get("detected_host_tools", []),
            "target_repository_mutation": False,
        })
        if installation_footprint.get("traversal", {}).get("status") == "blocked":
            attention.append({
                "id": "embedded_sage_footprint_not_host_traversable",
                "reason": "one or more declared SAGE footprint paths could not be traversed by the active account",
                "blocked_paths": installation_footprint.get("traversal", {}).get("blocked_paths", []),
                "permission_mutation": False,
            })

    if not target["exists"] or not target["is_dir"] or target["preflight_status"] == "FAIL":
        blockers.append({
            "id": "target_preflight_not_usable",
            "reason": "target repository is missing, unreadable or failed canonical preflight",
            "required_action": "provide a readable target repository and rerun the installation plan",
        })
    elif target["preflight_status"] == "ATTENTION":
        attention.append({
            "id": "target_preflight_attention",
            "reason": target.get("attention_reasons") or ["target capability is bounded"],
        })

    python_state = machine_state.get("python", {})
    if not python_state.get("requirement_satisfied"):
        blockers.append({
            "id": "supported_python_runtime_required",
            "reason": f"active Python {python_state.get('version')} does not satisfy {python_state.get('requirement')}",
            "required_action": "install a supported Python runtime explicitly, reopen the terminal and rerun the plan",
            "automatic_install": False,
        })
    elif not python_state.get("release_tested"):
        attention.append({
            "id": "python_runtime_outside_release_test_matrix",
            "reason": f"Python {python_state.get('minor')} satisfies metadata but is outside {python_state.get('tested_versions')}",
        })

    missing_features = [
        feature
        for feature, state in machine_state.get("python_features", {}).items()
        if state.get("required_by_profile") and not state.get("available")
    ]
    if missing_features:
        if machine_state.get("pip", {}).get("available"):
            actions.append({
                "id": "install_sage_python_dependencies",
                **_dependency_action_metadata(
                    contract, "install_sage_python_dependencies"
                ),
                "scope": "sage_installation",
                "reason": "default human-and-AI profile dependencies are missing",
                "missing_features": sorted(missing_features),
                "command": _resolved_command(contract.get("python", {}).get("package_install_command", [])),
                "automatic_on_init": True,
                "target_repository_mutation": False,
            })
        else:
            blockers.append({
                "id": "pip_required_for_sage_dependencies",
                "outcome_class": "MISSING_MANAGER",
                "dependency_authority": "sage_runtime",
                "reason": f"missing profile features require packages: {sorted(missing_features)}",
                "required_action": "enable pip for the active Python interpreter and rerun the plan",
                "automatic_install": False,
            })

    node_languages = set(str(value) for value in node_contract.get("required_for_languages", []))
    observed_languages = set(target.get("language_families") or [])
    node_required = bool(node_languages & observed_languages) or (
        bool(node_contract.get("required_when_react_signal"))
        and bool(target.get("react_signal"))
    )
    node_state = machine_state.get("node", {})
    npm_state = machine_state.get("npm", {})
    if bundled_typescript_available is None:
        node_cache = node_ast_cache_state(node_contract)
    else:
        node_cache = {
            "status": "VERIFIED" if bundled_typescript_available else "REINSTALL_REQUIRED",
            "content_identity": "test_override",
            "expected_typescript_version": None,
            "locked_typescript_version": None,
            "installed_typescript_version": None,
            "errors": [] if bundled_typescript_available else ["test_override_missing"],
            "claim_boundary": "Test-only cache availability override.",
        }
    bundled_available = node_cache.get("status") == "VERIFIED"

    if node_required and not node_state.get("available"):
        blockers.append({
            "id": "node_runtime_required_for_target_ast",
            "reason": "JavaScript/TypeScript or React source requires the Node-backed AST sequencer",
            "required_action": "install Node.js explicitly, reopen the terminal and rerun the plan",
            "release_reference_major": node_contract.get("release_reference_major"),
            "automatic_install": False,
        })
    elif node_required:
        node_major = _major_version(node_state.get("version"))
        release_reference_major = node_contract.get("release_reference_major")
        if node_major != release_reference_major:
            attention.append({
                "id": "node_runtime_outside_release_reference",
                "reason": (
                    f"Node.js major {node_major or 'unknown'} is available, but release CI "
                    f"currently references major {release_reference_major}; this is not a "
                    "compatibility failure or proof of equivalent coverage"
                ),
                "observed_major": node_major,
                "release_reference_major": release_reference_major,
            })

    if node_required and node_state.get("available") and not bundled_available:
        if npm_state.get("available"):
            actions.append({
                "id": "install_sage_node_ast_dependencies",
                **_dependency_action_metadata(
                    contract, "install_sage_node_ast_dependencies"
                ),
                "scope": "sage_installation/tools/engines",
                "reason": "target requires Node-backed AST and the bundled TypeScript runtime is absent",
                "command": _resolved_command(
                    node_contract.get("package_install_command", []),
                    npm=npm_state.get("path") or "npm",
                ),
                "cwd": str(CODE_MAPS_DIR / "tools" / "engines"),
                "cache": node_cache,
                "automatic_on_init": True,
                "target_repository_mutation": False,
            })
        else:
            blockers.append({
                "id": "npm_required_for_ast_dependency_install",
                "outcome_class": "MISSING_MANAGER",
                "dependency_authority": "sage_runtime",
                "reason": "Node-backed AST is required and bundled TypeScript is absent",
                "required_action": "install npm explicitly or restore the declared bundled TypeScript runtime",
                "automatic_install": False,
            })

    if not machine_state.get("git", {}).get("available"):
        attention.append({
            "id": "git_unavailable_for_source_acquisition",
            "reason": "Git is not required after the SAGE source and target repository are already present",
        })

    if actions and not dependency_install_enabled:
        blockers.append({
            "id": "required_dependencies_missing_while_install_disabled",
            "reason": "--skip-deps was requested while required SAGE-local dependencies are missing",
            "required_action": "rerun init without --skip-deps or execute the declared installation commands",
            "blocked_action_ids": [action["id"] for action in actions],
        })

    if blockers:
        status = str(status_policy.get("blocked") or "BLOCKED")
    elif actions:
        status = str(status_policy.get("install_actions_available") or "READY_WITH_INSTALL_ACTIONS")
    elif attention:
        status = str(status_policy.get("attention") or "ATTENTION")
    else:
        status = str(status_policy.get("ready") or "READY")

    return {
        "meta": {
            "kind": "installation_preflight",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.core.installation_preflight",
            "contract": "config/installation_preflight_contract.json",
        },
        "summary": {
            "status": status,
            "profile": contract.get("profile", {}).get("id"),
            "target_language_families": target.get("language_families"),
            "node_required_for_target": node_required,
            "python_install_action_required": any(action["id"] == "install_sage_python_dependencies" for action in actions),
            "node_install_action_required": any(action["id"] == "install_sage_node_ast_dependencies" for action in actions),
            "node_ast_cache_status": node_cache.get("status"),
            "automatic_dependency_authorities": sorted({
                str(action.get("dependency_authority"))
                for action in actions
                if action.get("dependency_authority")
            }),
            "target_native_install_action_count": sum(
                1
                for action in actions
                if action.get("dependency_authority") == "target_native_validation"
            ),
            "action_count": len(actions),
            "blocker_count": len(blockers),
            "attention_count": len(attention),
            "dependency_install_enabled": bool(dependency_install_enabled),
            "installation_mode": installation_footprint.get("installation_mode"),
            "embedded_host_guidance_status": installation_footprint.get("applicability"),
        },
        "target": target,
        "machine": machine_state,
        "actions": actions,
        "blockers": blockers,
        "attention": attention,
        "installation_footprint": installation_footprint,
        "dependency_acquisition": {
            "contract": contract.get("dependency_acquisition", {}).get("contract"),
            "node_ast_cache": node_cache,
            "target_native_validation": contract.get(
                "dependency_acquisition", {}
            ).get("authorities", {}).get("target_native_validation", {}),
            "claim_boundary": contract.get(
                "dependency_acquisition", {}
            ).get("claim_boundary"),
        },
        "mutation_boundary": {
            "target_repository_mutation_allowed": False,
            "system_runtime_auto_install_allowed": False,
            "sage_local_package_install_allowed": bool(dependency_install_enabled),
            "target_native_dependency_auto_install_allowed": False,
            "target_host_tool_configuration_mutation_allowed": False,
            "filesystem_permission_mutation_allowed": False,
        },
        "claim_boundary": contract.get("claim_boundary"),
    }


def render_installation_plan(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    target = payload.get("target", {})
    lines = [
        "# Installation Preflight",
        "",
        f"- status: `{summary.get('status')}`",
        f"- profile: `{summary.get('profile')}`",
        f"- target: `{target.get('root')}`",
        f"- target languages: `{', '.join(summary.get('target_language_families') or []) or 'none observed'}`",
        f"- Node required for target: `{summary.get('node_required_for_target')}`",
        f"- install actions: `{summary.get('action_count')}`",
        f"- blockers: `{summary.get('blocker_count')}`",
        f"- attention: `{summary.get('attention_count')}`",
        "",
        "## Actions",
        "",
    ]
    if payload.get("actions"):
        for action in payload["actions"]:
            lines.append(
                f"- `{action.get('id')}` "
                f"(authority=`{action.get('dependency_authority')}`, "
                f"operation=`{action.get('operation_class')}`): "
                f"`{' '.join(action.get('command') or [])}`"
            )
    else:
        lines.append("- None.")
    lines.extend(["", "## Blockers", ""])
    if payload.get("blockers"):
        for blocker in payload["blockers"]:
            lines.append(f"- `{blocker.get('id')}`: {blocker.get('required_action')}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Attention", ""])
    if payload.get("attention"):
        for item in payload["attention"]:
            lines.append(f"- `{item.get('id')}`: {item.get('reason')}")
    else:
        lines.append("- None.")
    footprint = payload.get("installation_footprint", {})
    lines.extend([
        "",
        "## Embedded Host-Tool Footprint",
        "",
        f"- installation mode: `{footprint.get('installation_mode')}`",
        f"- applicability: `{footprint.get('applicability')}`",
        f"- embedded root: `{footprint.get('embedded_root') or 'not applicable'}`",
        f"- detected host tools: `{', '.join(footprint.get('detected_host_tools') or []) or 'none'}`",
        f"- traversal: `{footprint.get('traversal', {}).get('status')}`",
        "- target configuration mutated: `False`",
    ])
    if footprint.get("host_tool_guidance"):
        lines.extend(["", "### Non-Mutating Guidance", ""])
        for item in footprint["host_tool_guidance"]:
            pattern = item.get("recommended_exclusion", {}).get("recursive_glob")
            lines.append(
                f"- `{item.get('id')}` ({item.get('surface')}): review exclusion `{pattern}` in target-owned configuration."
            )
    lines.extend(["", "## Boundary", "", str(payload.get("claim_boundary") or ""), ""])
    return "\n".join(lines)


def render_console_lines(payload: dict[str, Any]) -> list[str]:
    summary = payload.get("summary", {})
    lines = [
        f"[INSTALL-PLAN] status={summary.get('status')} profile={summary.get('profile')}",
        "[INSTALL-PLAN] target_languages="
        + (",".join(summary.get("target_language_families") or []) or "none")
        + f" node_required={summary.get('node_required_for_target')}",
        "[INSTALL-PLAN] installation_mode="
        + str(summary.get("installation_mode"))
        + " embedded_host_guidance="
        + str(summary.get("embedded_host_guidance_status")),
    ]
    for action in payload.get("actions", []):
        lines.append(
            f"[INSTALL-PLAN] action={action.get('id')} "
            f"authority={action.get('dependency_authority')} "
            f"operation={action.get('operation_class')} "
            f"command={' '.join(action.get('command') or [])}"
        )
    for blocker in payload.get("blockers", []):
        lines.append(f"[INSTALL-PLAN] blocker={blocker.get('id')} next={blocker.get('required_action')}")
    for item in payload.get("attention", []):
        lines.append(f"[INSTALL-PLAN] attention={item.get('id')} reason={item.get('reason')}")
    return lines


def write_installation_plan(payload: dict[str, Any]) -> None:
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_installation_plan(payload))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a non-mutating target-aware SAGE installation plan.")
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--skip-deps", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    payload = build_installation_plan(
        args.target_root,
        dependency_install_enabled=not args.skip_deps,
    )
    if not args.no_write:
        write_installation_plan(payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 2 if payload.get("summary", {}).get("status") == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
