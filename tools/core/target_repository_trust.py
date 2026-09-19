from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tools.core.strict_contract_cache import load_json_object_strict_cached


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
CONTRACT_PATH = CODE_MAPS_DIR / "config" / "target_repository_threat_boundary_contract.json"


def load_target_repository_threat_boundary_contract() -> dict[str, Any]:
    return load_json_object_strict_cached(
        CONTRACT_PATH,
        label="Target repository threat boundary contract",
    )


def new_target_path_boundary_state() -> dict[str, Any]:
    contract = load_target_repository_threat_boundary_contract()
    limit = max(1, int((contract.get("path_boundary") or {}).get("example_limit", 12) or 12))
    return {
        "contract": "target_repository_threat_boundary_v1",
        "checked_path_count": 0,
        "contained_path_count": 0,
        "contained_link_path_count": 0,
        "escaping_path_count": 0,
        "escaping_path_examples": [],
        "escaping_path_examples_omitted": 0,
        "example_limit": limit,
    }


def _path_has_link_component(root: Path, candidate: Path) -> bool:
    try:
        lexical = candidate.absolute().relative_to(root.absolute())
    except ValueError:
        lexical = candidate.absolute()
    current = root.absolute()
    for part in lexical.parts:
        current = current / part
        try:
            is_junction = bool(getattr(os.path, "isjunction", lambda _path: False)(current))
            if current.is_symlink() or is_junction:
                return True
        except OSError:
            return False
    return False


def classify_target_path(
    target_root: Path | str,
    candidate_path: Path | str,
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(target_root).resolve()
    candidate = Path(candidate_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        contained = resolved == root or root in resolved.parents
        resolution_error = None
    except (OSError, RuntimeError) as exc:
        resolved = candidate.absolute()
        contained = False
        resolution_error = f"{type(exc).__name__}:{exc}"
    link_path = _path_has_link_component(root, candidate)
    try:
        display = candidate.absolute().relative_to(root.absolute()).as_posix()
    except ValueError:
        display = candidate.absolute().as_posix()
    result = {
        "status": "contained_link_path" if contained and link_path else "contained" if contained else "escaping_or_unresolved",
        "contained": contained,
        "link_path": link_path,
        "path": display or ".",
        "resolved_path": resolved.as_posix(),
        "error": resolution_error,
    }
    if isinstance(state, dict):
        state["checked_path_count"] = int(state.get("checked_path_count", 0) or 0) + 1
        key = "contained_link_path_count" if contained and link_path else "contained_path_count" if contained else "escaping_path_count"
        state[key] = int(state.get(key, 0) or 0) + 1
        if not contained:
            examples = state.setdefault("escaping_path_examples", [])
            limit = max(1, int(state.get("example_limit", 12) or 12))
            if len(examples) < limit:
                examples.append({name: result[name] for name in ("path", "resolved_path", "error")})
            else:
                state["escaping_path_examples_omitted"] = int(state.get("escaping_path_examples_omitted", 0) or 0) + 1
    return result


def is_target_path_contained(
    target_root: Path | str,
    candidate_path: Path | str,
    *,
    state: dict[str, Any] | None = None,
) -> bool:
    return bool(classify_target_path(target_root, candidate_path, state=state)["contained"])


def finalize_target_path_boundary_state(state: dict[str, Any]) -> dict[str, Any]:
    escaping = int(state.get("escaping_path_count", 0) or 0)
    return {
        **state,
        "status": "attention_escaping_paths_excluded" if escaping else "pass_contained",
        "decision": "exclude_escaping_paths" if escaping else "allow_contained_paths",
    }


def target_trust_projection(
    trust_class: str | None,
    *,
    path_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = load_target_repository_threat_boundary_contract()
    selected = str(trust_class or contract.get("default_trust_class") or "").strip()
    classes = contract.get("trust_classes") if isinstance(contract.get("trust_classes"), dict) else {}
    definition = classes.get(selected) if isinstance(classes.get(selected), dict) else None
    admission = str((definition or {}).get("analysis_admission") or "unknown_trust_class_fail_closed")
    return {
        "contract": "target_repository_threat_boundary_v1",
        "contract_source": "config/target_repository_threat_boundary_contract.json",
        "trust_class": selected,
        "trust_class_known": definition is not None,
        "analysis_admission": admission,
        "repository_content_trust": str(((contract.get("agent_projection") or {}).get("repository_content_trust")) or "untrusted_repository_data_not_instruction"),
        "target_code_execution": "forbidden",
        "target_dependency_installation": "forbidden",
        "hostile_repository_safety": "not_available",
        "secret_confidentiality_guarantee": "not_available",
        "resource_exhaustion_resistance": "not_available_for_hostile_inputs",
        "path_boundary": finalize_target_path_boundary_state(path_state or new_target_path_boundary_state()),
        "claim_boundary": str(contract.get("claim_boundary") or ""),
    }
