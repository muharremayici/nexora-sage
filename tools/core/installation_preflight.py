from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
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
        "source": "tools.external_target_preflight.build_preflight",
    }


def _resolved_command(template: list[str], *, npm: str | None = None) -> list[str]:
    values = {
        "{python}": sys.executable,
        "{sage_root}": str(CODE_MAPS_DIR),
        "{npm}": str(npm or "npm"),
    }
    return [values.get(str(value), str(value)) for value in template]


def build_installation_plan(
    target_root: str | Path,
    *,
    target_preflight: dict[str, Any] | None = None,
    target_profile: dict[str, Any] | None = None,
    machine: dict[str, Any] | None = None,
    feature_availability: dict[str, bool] | None = None,
    bundled_typescript_available: bool | None = None,
    dependency_install_enabled: bool = True,
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
    marker = CODE_MAPS_DIR / str(node_contract.get("bundled_typescript_marker") or "")
    bundled_available = marker.is_file() if bundled_typescript_available is None else bool(bundled_typescript_available)

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
                "scope": "sage_installation/tools/engines",
                "reason": "target requires Node-backed AST and the bundled TypeScript runtime is absent",
                "command": _resolved_command(
                    node_contract.get("package_install_command", []),
                    npm=npm_state.get("path") or "npm",
                ),
                "cwd": str(CODE_MAPS_DIR / "tools" / "engines"),
                "automatic_on_init": True,
                "target_repository_mutation": False,
            })
        else:
            blockers.append({
                "id": "npm_required_for_ast_dependency_install",
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
            "action_count": len(actions),
            "blocker_count": len(blockers),
            "attention_count": len(attention),
            "dependency_install_enabled": bool(dependency_install_enabled),
        },
        "target": target,
        "machine": machine_state,
        "actions": actions,
        "blockers": blockers,
        "attention": attention,
        "mutation_boundary": {
            "target_repository_mutation_allowed": False,
            "system_runtime_auto_install_allowed": False,
            "sage_local_package_install_allowed": bool(dependency_install_enabled),
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
            lines.append(f"- `{action.get('id')}`: `{' '.join(action.get('command') or [])}`")
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
    lines.extend(["", "## Boundary", "", str(payload.get("claim_boundary") or ""), ""])
    return "\n".join(lines)


def render_console_lines(payload: dict[str, Any]) -> list[str]:
    summary = payload.get("summary", {})
    lines = [
        f"[INSTALL-PLAN] status={summary.get('status')} profile={summary.get('profile')}",
        "[INSTALL-PLAN] target_languages="
        + (",".join(summary.get("target_language_families") or []) or "none")
        + f" node_required={summary.get('node_required_for_target')}",
    ]
    for action in payload.get("actions", []):
        lines.append(f"[INSTALL-PLAN] action={action.get('id')} command={' '.join(action.get('command') or [])}")
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
