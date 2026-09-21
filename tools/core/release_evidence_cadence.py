from __future__ import annotations

import ast
from copy import deepcopy
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict_cached
from tools.core.unmanaged_atomic_io import native_filesystem_path
from tools.core.release_validation_dependencies import (
    resolve_local_python_import_closure,
)
from tools.core.source_layer_classifier import classify_source_layer


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = CONFIG_DIR / "release_evidence_cadence_contract.json"

SUPPORTED_CADENCES = frozenset(
    {
        "always_fresh",
        "change_triggered",
        "baseline_plus_delta",
        "periodic",
        "final_candidate_aggregate",
    }
)
SUPPORTED_AGGREGATE_STRATEGIES = frozenset(
    {
        "validate_content_bound_receipts_and_execute_invalidated_or_always_fresh",
        "fresh_execution",
    }
)
SUPPORTED_DISPOSITIONS = frozenset(
    {
        "run_fresh",
        "baseline_plus_delta",
        "not_required",
        "blocked_missing_baseline",
    }
)
REQUIRED_CONTEXT_FIELDS = frozenset(
    {"release_phase", "triggers", "available_baselines"}
)
AUTHORITATIVE_PROOF_DISPATCH_AUTHORITY = (
    "execution_dispatch_only_no_release_or_publication_authority"
)
AUTHORITATIVE_FROZEN_AGGREGATE_ACTION = "run_current_frozen_candidate_aggregate"
AUTHORITATIVE_FOCUSED_ACTION = "run_selected_dependency_closure"
NON_AUTHORITATIVE_FULL_ACTION = "run_non_authoritative_full_diagnostic"
NON_AUTHORITATIVE_FOCUSED_ACTION = "run_non_authoritative_selected_closure"
AUTHORITATIVE_FROZEN_AGGREGATE_STRATEGY = (
    "validate_content_bound_receipts_and_execute_invalidated_or_always_fresh"
)
DISPATCH_EVENT_SCHEMA = "v2_chained"
CADENCE_CONTRACT_IDENTITY_SCHEMA = "v1_content_sha256"
DISPATCH_EVENT_DIRECTORY = PurePosixPath(
    "output/.operational/release_proof/dispatch_events"
)
INSTALLATION_DERIVATION_REQUIRED_PROOF_STEPS = frozenset(
    {"installation_contract", "installation_proof_smoke"}
)
INSTALLATION_DERIVATION_REQUIRED_AUTHORITY_PATHS = frozenset(
    {
        "config/release_evidence_cadence_contract.json",
        "config/cli_command_contract.json",
        "config/installation_preflight_contract.json",
        "config/external_clean_machine_evidence_contract.json",
        "config/source_role_obligation_contract.json",
        "config/release_proof_steps_contract.json",
        "tools/core/release_evidence_cadence.py",
        "tools/derive_release_evidence_cadence.py",
        "tools/core/release_validation_dependencies.py",
        "tools/core/source_layer_classifier.py",
        "tools/core/source_layer_classification_policy.py",
        "config/source_layer_classification_policy.json",
        "config/source_layer_taxonomy.json",
    }
)
CADENCE_RECEIPT_KIND = "nexora.release_evidence_cadence_receipt"
CADENCE_RECEIPT_VERSION = "v1"
INSTALLATION_DERIVATION_REQUIRED_LAYER_TRIGGERS = {
    "installation_onboarding": "bootstrap_semantics_changed",
    "package_runtime": "dependency_semantics_changed",
}


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def dispatch_semantic_payload(decision: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = deepcopy(dict(decision)) if isinstance(decision, Mapping) else {}
    payload.pop("history", None)
    payload.pop("dispatch_event_binding", None)
    meta = payload.get("meta")
    if isinstance(meta, dict):
        for field in (
            "generated_at",
            "dispatch_id",
            "decision_semantic_sha256",
            "event_sha256",
            "dispatch_event_schema",
            "history_sequence",
            "previous_event_sha256",
        ):
            meta.pop(field, None)
    return payload


def dispatch_semantic_sha256(decision: Mapping[str, Any] | None) -> str:
    return _canonical_sha256(dispatch_semantic_payload(decision))


def dispatch_event_sha256(event: Mapping[str, Any] | None) -> str:
    payload = deepcopy(dict(event)) if isinstance(event, Mapping) else {}
    meta = payload.get("meta")
    if isinstance(meta, dict):
        meta.pop("event_sha256", None)
    return _canonical_sha256(payload)


def build_dispatch_event(
    decision: Mapping[str, Any],
    *,
    history_sequence: int,
    previous_event_sha256: str | None,
) -> dict[str, Any]:
    event = deepcopy(dict(decision))
    event.pop("history", None)
    event.pop("dispatch_event_binding", None)
    meta = event.get("meta")
    if not isinstance(meta, dict) or not str(meta.get("dispatch_id") or ""):
        raise ValueError("Dispatch event requires a dispatch id")
    if history_sequence < 1:
        raise ValueError("Dispatch event history sequence must be positive")
    if history_sequence == 1:
        if previous_event_sha256 is not None:
            raise ValueError("Genesis dispatch event must not declare a predecessor")
    elif (
        not isinstance(previous_event_sha256, str)
        or len(previous_event_sha256) != 64
        or any(character not in "0123456789abcdef" for character in previous_event_sha256)
    ):
        raise ValueError("Chained dispatch event requires a valid predecessor digest")
    meta["dispatch_event_schema"] = DISPATCH_EVENT_SCHEMA
    meta["history_sequence"] = history_sequence
    meta["previous_event_sha256"] = previous_event_sha256
    meta["decision_semantic_sha256"] = dispatch_semantic_sha256(event)
    meta["event_sha256"] = dispatch_event_sha256(event)
    return event


def dispatch_event_binding(event: Mapping[str, Any], relative_path: str) -> dict[str, Any]:
    meta = event.get("meta") if isinstance(event.get("meta"), Mapping) else {}
    return {
        "dispatch_id": str(meta.get("dispatch_id") or ""),
        "relative_path": relative_path,
        "decision_semantic_sha256": str(meta.get("decision_semantic_sha256") or ""),
        "event_sha256": str(meta.get("event_sha256") or ""),
        "dispatch_event_schema": str(meta.get("dispatch_event_schema") or ""),
        "history_sequence": meta.get("history_sequence"),
        "previous_event_sha256": meta.get("previous_event_sha256"),
    }


def verify_dispatch_event_binding(
    binding: Mapping[str, Any] | None,
    decision: Mapping[str, Any] | None,
    *,
    repository_root: Path = ROOT,
) -> dict[str, Any]:
    reasons: list[str] = []
    supplied = binding if isinstance(binding, Mapping) else {}
    relative_text = str(supplied.get("relative_path") or "")
    relative = PurePosixPath(relative_text)
    expected_parent = DISPATCH_EVENT_DIRECTORY
    if (
        not relative_text
        or "\\" in relative_text
        or relative.is_absolute()
        or relative.as_posix() != relative_text
        or relative.parent != expected_parent
        or relative.suffix != ".json"
        or ".." in relative.parts
    ):
        reasons.append("dispatch_event_path_invalid")
        candidate = None
    else:
        candidate = repository_root / Path(*relative.parts)
    event: dict[str, Any] = {}
    if candidate is None:
        reasons.append("dispatch_event_missing")
    else:
        try:
            loaded = json.loads(
                Path(native_filesystem_path(candidate)).read_text(encoding="utf-8")
            )
            event = loaded if isinstance(loaded, dict) else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            reasons.append("dispatch_event_missing_or_invalid_json")
    meta = event.get("meta") if isinstance(event.get("meta"), Mapping) else {}
    dispatch_id = str(meta.get("dispatch_id") or "")
    if not dispatch_id or supplied.get("dispatch_id") != dispatch_id:
        reasons.append("dispatch_event_id_mismatch")
    if candidate is not None and candidate.name != f"{dispatch_id}.json":
        reasons.append("dispatch_event_filename_mismatch")
    semantic_sha = dispatch_semantic_sha256(event)
    decision_projection = release_proof_dispatch_projection(decision)
    event_projection = release_proof_dispatch_projection(event)
    decision_boundary_state = release_proof_dispatch_boundary_state(decision)
    event_boundary_state = release_proof_dispatch_boundary_state(event)
    if decision_boundary_state["state_known"] is not True:
        reasons.append("dispatch_decision_external_boundary_state_unknown")
    if event_boundary_state["state_known"] is not True:
        reasons.append("dispatch_event_external_boundary_state_unknown")
    if (
        not semantic_sha
        or meta.get("decision_semantic_sha256") != semantic_sha
        or supplied.get("decision_semantic_sha256") != semantic_sha
        or decision_projection.get("decision_semantic_sha256") != semantic_sha
    ):
        reasons.append("dispatch_event_semantic_digest_mismatch")
    event_sha = dispatch_event_sha256(event)
    if (
        not event_sha
        or meta.get("event_sha256") != event_sha
        or supplied.get("event_sha256") != event_sha
    ):
        reasons.append("dispatch_event_content_digest_mismatch")
    if event_projection != decision_projection:
        reasons.append("dispatch_event_decision_projection_mismatch")
    sequence = meta.get("history_sequence")
    previous_event_sha256 = meta.get("previous_event_sha256")
    if (
        meta.get("dispatch_event_schema") != DISPATCH_EVENT_SCHEMA
        or supplied.get("dispatch_event_schema") != DISPATCH_EVENT_SCHEMA
    ):
        reasons.append("dispatch_event_schema_mismatch")
    if (
        not isinstance(sequence, int)
        or sequence < 1
        or supplied.get("history_sequence") != sequence
    ):
        reasons.append("dispatch_event_sequence_mismatch")
    if supplied.get("previous_event_sha256") != previous_event_sha256:
        reasons.append("dispatch_event_predecessor_mismatch")
    if sequence == 1 and previous_event_sha256 is not None:
        reasons.append("dispatch_event_genesis_predecessor_invalid")
    if sequence != 1 and (
        not isinstance(previous_event_sha256, str)
        or len(previous_event_sha256) != 64
        or any(character not in "0123456789abcdef" for character in previous_event_sha256)
    ):
        reasons.append("dispatch_event_predecessor_digest_invalid")
    if not recorded_cadence_contract_identity_valid(meta.get("cadence_contract_identity")):
        reasons.append("dispatch_event_cadence_contract_identity_invalid")
    return {
        "valid": not reasons,
        "reasons": sorted(set(reasons)) or ["dispatch_event_binding_verified"],
        "dispatch_id": dispatch_id or None,
        "event_sha256": event_sha if event else None,
    }


def _exact_string_list_or_none(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if any(
        not isinstance(item, str) or not item or item.strip() != item
        for item in value
    ):
        return None
    if len(value) != len(set(value)):
        return None
    return list(value)


def release_proof_dispatch_projection(decision: Mapping[str, Any] | None) -> dict[str, Any]:
    source = decision if isinstance(decision, Mapping) else {}
    request = source.get("request") if isinstance(source.get("request"), Mapping) else {}
    meta = source.get("meta") if isinstance(source.get("meta"), Mapping) else {}
    external_authority = (
        source.get("external_evidence_authority")
        if isinstance(source.get("external_evidence_authority"), Mapping)
        else {}
    )
    return {
        "dispatch_id": meta.get("dispatch_id") or source.get("dispatch_id"),
        "status": source.get("status"),
        "allowed": source.get("allowed"),
        "action": source.get("action"),
        "reason": source.get("reason"),
        "proof_scope": request.get("proof_scope") or source.get("proof_scope"),
        "selected_step_ids": list(
            request.get("selected_step_ids") or source.get("selected_step_ids") or []
        ),
        "release_phase": request.get("release_phase") or source.get("release_phase"),
        "triggers": list(request.get("triggers") or source.get("triggers") or []),
        "available_baselines": list(
            request.get("available_baselines") or source.get("available_baselines") or []
        ),
        "non_authoritative_diagnostic": (
            request.get("non_authoritative_diagnostic")
            if "non_authoritative_diagnostic" in request
            else source.get("non_authoritative_diagnostic")
        ),
        "aggregate_strategy": source.get("aggregate_strategy"),
        "authority": meta.get("authority") or source.get("authority"),
        "cadence_contract_identity": dict(
            meta.get("cadence_contract_identity")
            if isinstance(meta.get("cadence_contract_identity"), Mapping)
            else source.get("cadence_contract_identity")
            if isinstance(source.get("cadence_contract_identity"), Mapping)
            else {}
        ),
        "decision_semantic_sha256": (
            meta.get("decision_semantic_sha256")
            or source.get("decision_semantic_sha256")
            or dispatch_semantic_sha256(source)
        ),
        "external_evidence_authority": dict(external_authority),
        "release_train_blocked_evidence": _exact_string_list_or_none(
            source.get("release_train_blocked_evidence")
        ),
        "always_fresh_external_boundaries": _exact_string_list_or_none(
            source.get("always_fresh_external_boundaries")
        ),
    }


def release_proof_dispatch_boundary_state(
    decision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    projection = release_proof_dispatch_projection(decision)
    blocked = projection.get("release_train_blocked_evidence")
    always_fresh = projection.get("always_fresh_external_boundaries")
    state_known = isinstance(blocked, list) and isinstance(always_fresh, list)
    return {
        "state_known": state_known,
        "release_train_blocked_evidence": list(blocked) if isinstance(blocked, list) else None,
        "always_fresh_external_boundaries": (
            list(always_fresh) if isinstance(always_fresh, list) else None
        ),
        "machine_public_release_evidence_unblocked": state_known and not blocked,
    }


def authoritative_frozen_aggregate_dispatch(
    decision: Mapping[str, Any] | None,
) -> bool:
    projection = release_proof_dispatch_projection(decision)
    boundary_state = release_proof_dispatch_boundary_state(decision)
    return (
        projection.get("status") == "ALLOWED"
        and projection.get("allowed") is True
        and projection.get("proof_scope") == "full_release_proof"
        and projection.get("release_phase") == "frozen_candidate"
        and projection.get("non_authoritative_diagnostic") is False
        and projection.get("action") == AUTHORITATIVE_FROZEN_AGGREGATE_ACTION
        and projection.get("aggregate_strategy")
        == AUTHORITATIVE_FROZEN_AGGREGATE_STRATEGY
        and projection.get("authority") == AUTHORITATIVE_PROOF_DISPATCH_AUTHORITY
        and boundary_state.get("state_known") is True
    )


def allowed_release_proof_dispatch(
    decision: Mapping[str, Any] | None,
    *,
    proof_scope: str,
    selected_step_ids: list[str],
) -> bool:
    projection = release_proof_dispatch_projection(decision)
    boundary_state = release_proof_dispatch_boundary_state(decision)
    if (
        projection.get("allowed") is not True
        or projection.get("proof_scope") != proof_scope
        or projection.get("selected_step_ids") != selected_step_ids
        or projection.get("authority") != AUTHORITATIVE_PROOF_DISPATCH_AUTHORITY
        or boundary_state.get("state_known") is not True
    ):
        return False
    if projection.get("non_authoritative_diagnostic") is True:
        expected_action = (
            NON_AUTHORITATIVE_FOCUSED_ACTION
            if proof_scope == "selected_step_closure"
            else NON_AUTHORITATIVE_FULL_ACTION
        )
        return (
            projection.get("status") == "ALLOWED_NON_AUTHORITATIVE_DIAGNOSTIC"
            and projection.get("action") == expected_action
        )
    if proof_scope == "full_release_proof":
        return authoritative_frozen_aggregate_dispatch(decision)
    return (
        projection.get("status") == "ALLOWED"
        and projection.get("action") == AUTHORITATIVE_FOCUSED_ACTION
        and projection.get("release_phase") in {"development", "candidate"}
        and projection.get("non_authoritative_diagnostic") is False
    )


def _string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "a string list" if allow_empty else "a non-empty string list"
        raise ValueError(f"{label} must be {qualifier}")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} must contain only non-empty strings")
        normalized.append(item.strip())
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must not contain duplicates")
    return normalized


def _safe_repository_source(root: Path, source: str) -> bool:
    posix = PurePosixPath(source)
    if (
        not source
        or "\\" in source
        or posix.is_absolute()
        or posix.as_posix() != source
        or ".." in posix.parts
    ):
        return False
    candidate = (root / Path(*posix.parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return False
    return candidate.is_file()


def _safe_private_source(source: str) -> bool:
    if not source.startswith("private://") or "\\" in source:
        return False
    relative = PurePosixPath(source.removeprefix("private://"))
    return bool(relative.parts) and not relative.is_absolute() and ".." not in relative.parts


def load_release_evidence_cadence_contract() -> dict[str, Any]:
    contract = load_json_object_strict_cached(
        CONTRACT_PATH,
        label="Release evidence cadence contract",
    )
    validate_release_evidence_cadence_contract(contract)
    return contract


def release_evidence_cadence_contract_identity(
    contract: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    selected = dict(contract) if isinstance(contract, Mapping) else load_release_evidence_cadence_contract()
    meta = selected.get("meta") if isinstance(selected.get("meta"), Mapping) else {}
    return {
        "identity_schema": CADENCE_CONTRACT_IDENTITY_SCHEMA,
        "kind": str(meta.get("kind") or ""),
        "contract_version": str(meta.get("version") or ""),
        "content_sha256": _canonical_sha256(selected),
    }


def recorded_cadence_contract_identity_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    digest = str(value.get("content_sha256") or "")
    return (
        value.get("identity_schema") == CADENCE_CONTRACT_IDENTITY_SCHEMA
        and value.get("kind") == "nexora.release_evidence_cadence_contract"
        and bool(str(value.get("contract_version") or ""))
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
        and set(value)
        == {"identity_schema", "kind", "contract_version", "content_sha256"}
    )


def _normalized_repository_relative_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().replace("\\", "/")
    candidate = PurePosixPath(raw)
    if (
        not raw
        or candidate.is_absolute()
        or raw in {".", ".."}
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or (candidate.parts and ":" in candidate.parts[0])
    ):
        return None
    return candidate.as_posix()


def _drop_json_path(document: Mapping[str, Any], dotted_path: str) -> dict[str, Any]:
    cloned = deepcopy(dict(document))
    parts = [part for part in str(dotted_path).split(".") if part]
    if not parts:
        return cloned
    cursor: Any = cloned
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            return cloned
        cursor = cursor[part]
    if isinstance(cursor, dict):
        cursor.pop(parts[-1], None)
    return cloned


def _load_contract_at(root: Path, relative: str, *, label: str) -> dict[str, Any]:
    normalized = _normalized_repository_relative_path(relative)
    if normalized is None:
        raise ValueError(f"Unsafe {label} path: {relative}")
    path = (root / normalized).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        raise ValueError(f"Missing {label}: {normalized}")
    return load_json_object_strict_cached(path, label=label)


def _installation_trigger_closure(
    contract: Mapping[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    root = root.resolve()
    derivation = contract.get("installation_trigger_derivation")
    reasons: list[str] = []
    path_triggers: dict[str, set[str]] = {}

    def add_path(raw_path: Any, trigger: Any, owner: str) -> None:
        relative = _normalized_repository_relative_path(raw_path)
        trigger_id = str(trigger or "").strip()
        if relative is None:
            reasons.append(f"unsafe_installation_owner_path:{owner}:{raw_path}")
            return
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            reasons.append(f"missing_installation_owner_path:{owner}:{relative}")
            return
        if not trigger_id:
            reasons.append(f"missing_installation_owner_trigger:{owner}:{relative}")
            return
        path_triggers.setdefault(relative, set()).add(trigger_id)

    if not isinstance(derivation, Mapping):
        return {
            "status": "BLOCKED",
            "path_triggers": {},
            "files": [],
            "content_sha256": _canonical_sha256([]),
            "reasons": ["installation_trigger_derivation_missing"],
        }

    for row in derivation.get("exact_authority_paths", []):
        if not isinstance(row, Mapping):
            reasons.append("invalid_exact_installation_authority_row")
            continue
        add_path(row.get("path"), row.get("trigger"), "exact_authority")

    try:
        role_contract = _load_contract_at(
            root,
            str(derivation.get("source_role_contract") or ""),
            label="source role obligation contract",
        )
        root_role = str(derivation.get("root_entrypoint_role") or "")
        role_rows = [
            row
            for row in role_contract.get("role_rules", [])
            if isinstance(row, Mapping) and row.get("role") == root_role
        ]
        if len(role_rows) != 1:
            reasons.append("installation_root_entrypoint_role_not_unique")
        cli_contract = _load_contract_at(
            root,
            "config/cli_command_contract.json",
            label="CLI command contract",
        )
        install_rows = [
            row
            for row in cli_contract.get("commands", [])
            if isinstance(row, Mapping) and row.get("id") == "install_proof"
        ]
        if len(install_rows) != 1:
            reasons.append("install_proof_cli_command_not_unique")
        elif role_rows:
            surface_tokens = str(install_rows[0].get("surface") or "").split()
            entrypoints = [
                token.strip('"\'')
                for token in surface_tokens
                if token.strip('"\'').endswith(".py")
            ]
            declared_entrypoints = set(role_rows[0].get("exact_paths") or [])
            if len(entrypoints) != 1 or entrypoints[0] not in declared_entrypoints:
                reasons.append("install_proof_entrypoint_not_bound_to_source_role")
            else:
                entrypoint = entrypoints[0]
                add_path(entrypoint, "bootstrap_semantics_changed", "install_proof_cli")
                entry_path = root / entrypoint
                try:
                    tree = ast.parse(entry_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, SyntaxError) as exc:
                    reasons.append(
                        f"install_proof_entrypoint_parse_failed:{type(exc).__name__}"
                    )
                else:
                    for node in tree.body:
                        module = ""
                        if isinstance(node, ast.ImportFrom):
                            module = str(node.module or "")
                        elif isinstance(node, ast.Import) and len(node.names) == 1:
                            module = node.names[0].name
                        root_module = module.split(".")[0]
                        candidate = root / f"{root_module}.py"
                        if root_module and candidate.is_file():
                            add_path(
                                candidate.relative_to(root).as_posix(),
                                "bootstrap_semantics_changed",
                                "install_proof_entrypoint_direct_import",
                            )
    except ValueError as exc:
        reasons.append(f"installation_entrypoint_authority_invalid:{exc}")

    try:
        proof_contract = _load_contract_at(
            root,
            str(derivation.get("proof_steps_contract") or ""),
            label="release proof steps contract",
        )
        proof_rows = {
            str(row.get("id")): row
            for row in proof_contract.get("steps", [])
            if isinstance(row, Mapping) and str(row.get("id") or "")
        }
        for requested in derivation.get("proof_steps", []):
            if not isinstance(requested, Mapping):
                reasons.append("invalid_installation_proof_step_owner")
                continue
            step_id = str(requested.get("id") or "")
            trigger = str(requested.get("trigger") or "")
            proof_row = proof_rows.get(step_id)
            if not isinstance(proof_row, Mapping):
                reasons.append(f"installation_proof_step_missing:{step_id}")
                continue
            seeds = []
            for token in proof_row.get("command", []):
                if not isinstance(token, str) or not token.startswith("${code_maps}/"):
                    continue
                relative = token[len("${code_maps}/"):]
                if relative.endswith(".py"):
                    seeds.append(relative)
            if not seeds:
                reasons.append(f"installation_proof_step_has_no_python_owner:{step_id}")
                continue
            closure = resolve_local_python_import_closure(seeds, root=root)
            if closure.get("status") != "COMPLETE":
                reasons.extend(
                    f"installation_proof_step_closure:{step_id}:{reason}"
                    for reason in closure.get("reasons", [])
                )
                continue
            for relative in closure.get("paths", []):
                add_path(relative, trigger, f"proof_step:{step_id}")
    except ValueError as exc:
        reasons.append(f"installation_proof_authority_invalid:{exc}")

    files: list[dict[str, Any]] = []
    for relative in sorted(path_triggers):
        content = (root / relative).read_bytes()
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "triggers": sorted(path_triggers[relative]),
            }
        )
    return {
        "status": "BLOCKED" if reasons else "COMPLETE",
        "path_triggers": {
            path: sorted(values) for path, values in sorted(path_triggers.items())
        },
        "files": files,
        "content_sha256": _canonical_sha256(files),
        "reasons": sorted(set(reasons)),
    }


def derive_installation_semantic_triggers(
    changed_paths: list[str] | tuple[str, ...] | set[str],
    *,
    semantic_metadata_documents: Mapping[str, Mapping[str, Any]] | None = None,
    contract: Mapping[str, Any] | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Derive protected installation triggers from central semantic owners.

    The returned receipt is cadence input only.  It neither accepts a historical
    baseline nor grants release, human or publication authority.
    """

    selected = dict(contract) if isinstance(contract, Mapping) else load_release_evidence_cadence_contract()
    validate_release_evidence_cadence_contract(selected, root=root)
    derivation = selected["installation_trigger_derivation"]
    closure = _installation_trigger_closure(selected, root=root)
    normalized_paths: list[str] = []
    blocking_reasons = list(closure.get("reasons") or [])
    for value in changed_paths:
        relative = _normalized_repository_relative_path(value)
        if relative is None:
            blocking_reasons.append(f"unsafe_changed_path:{value}")
        else:
            normalized_paths.append(relative)
    normalized_paths = sorted(set(normalized_paths))

    metadata_rows = {
        str(row.get("path")): row
        for row in derivation.get("semantic_metadata", [])
        if isinstance(row, Mapping)
    }
    metadata_documents = semantic_metadata_documents or {}
    classified: list[dict[str, Any]] = []
    derived_triggers: set[str] = set()
    unaffected_paths: list[str] = []
    protected_paths = closure.get("path_triggers") or {}
    layer_triggers = derivation.get("source_layer_triggers") or {}

    for relative in normalized_paths:
        row: dict[str, Any] = {"path": relative}
        if relative in metadata_rows:
            metadata_rule = metadata_rows[relative]
            evidence = metadata_documents.get(relative)
            before = evidence.get("before") if isinstance(evidence, Mapping) else None
            after = evidence.get("after") if isinstance(evidence, Mapping) else None
            if not isinstance(before, Mapping) or not isinstance(after, Mapping):
                reason = f"semantic_metadata_evidence_missing:{relative}"
                blocking_reasons.append(reason)
                row.update({"disposition": "blocked", "reason": reason, "triggers": []})
            else:
                normalized_before: Mapping[str, Any] = before
                normalized_after: Mapping[str, Any] = after
                for dotted in metadata_rule.get("ignored_json_paths", []):
                    normalized_before = _drop_json_path(normalized_before, str(dotted))
                    normalized_after = _drop_json_path(normalized_after, str(dotted))
                if normalized_before == normalized_after:
                    unaffected_paths.append(relative)
                    row.update(
                        {
                            "disposition": "unaffected",
                            "reason": "ignored_metadata_only_change",
                            "triggers": [],
                        }
                    )
                else:
                    triggers = sorted(set(metadata_rule.get("changed_triggers", [])))
                    derived_triggers.update(triggers)
                    row.update(
                        {
                            "disposition": "protected_change",
                            "reason": "semantic_metadata_changed",
                            "triggers": triggers,
                        }
                    )
                row["metadata_evidence_sha256"] = _canonical_sha256(
                    {"before": before, "after": after}
                )
        elif relative in protected_paths:
            triggers = sorted(set(protected_paths[relative]))
            derived_triggers.update(triggers)
            row.update(
                {
                    "disposition": "protected_change",
                    "reason": "declared_semantic_owner_or_transitive_dependency_changed",
                    "triggers": triggers,
                }
            )
        else:
            candidate = (root / relative).resolve()
            try:
                layer, layer_reason = classify_source_layer(candidate, root=root)
            except (OSError, ValueError) as exc:
                layer, layer_reason = "unknown_or_review", type(exc).__name__
            row["source_layer"] = layer
            trigger = layer_triggers.get(layer)
            if trigger:
                derived_triggers.add(str(trigger))
                row.update(
                    {
                        "disposition": "protected_change",
                        "reason": f"source_layer:{layer}:{layer_reason}",
                        "triggers": [str(trigger)],
                    }
                )
            elif layer == "unknown_or_review":
                reason = f"unknown_changed_path_owner:{relative}"
                blocking_reasons.append(reason)
                row.update({"disposition": "blocked", "reason": reason, "triggers": []})
            elif layer == "generated_runtime_artifact":
                reason = f"generated_runtime_path_in_governed_change:{relative}"
                blocking_reasons.append(reason)
                row.update({"disposition": "blocked", "reason": reason, "triggers": []})
            else:
                unaffected_paths.append(relative)
                row.update(
                    {
                        "disposition": "unaffected",
                        "reason": f"known_non_installation_layer:{layer}",
                        "triggers": [],
                    }
                )
        classified.append(row)

    contract_identity = release_evidence_cadence_contract_identity(selected)
    receipt: dict[str, Any] = {
        "meta": {
            "kind": "nexora.installation_semantic_trigger_receipt",
            "version": "v1",
            "derivation_schema": derivation.get("schema"),
        },
        "status": "BLOCKED" if blocking_reasons else "DERIVED",
        "changed_paths": normalized_paths,
        "changed_paths_sha256": _canonical_sha256(normalized_paths),
        "classified_paths": classified,
        "derived_triggers": sorted(derived_triggers),
        "unaffected_paths": sorted(set(unaffected_paths)),
        "blocking_reasons": sorted(set(blocking_reasons)),
        "contract_identity": contract_identity,
        "semantic_closure": {
            "status": closure.get("status"),
            "content_sha256": closure.get("content_sha256"),
            "files": closure.get("files"),
        },
        "authority": derivation.get("receipt_authority"),
        "baseline_accepted": False,
        "publication_authority": False,
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    return receipt


def build_release_evidence_cadence_receipt(
    context: Mapping[str, Any],
    *,
    changed_paths: list[str] | tuple[str, ...] | set[str],
    semantic_metadata_documents: Mapping[str, Mapping[str, Any]] | None = None,
    contract: Mapping[str, Any] | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Bind semantic installation invalidation to the central cadence decision.

    The receipt is deterministic cadence input.  It does not validate a claimed
    historical baseline, execute any evidence, accept a release, or authorize
    publication.  Those responsibilities remain with the existing private
    release controller.
    """

    selected = (
        dict(contract)
        if isinstance(contract, Mapping)
        else load_release_evidence_cadence_contract()
    )
    validate_release_evidence_cadence_contract(selected, root=root)
    derivation = derive_installation_semantic_triggers(
        changed_paths,
        semantic_metadata_documents=semantic_metadata_documents,
        contract=selected,
        root=root,
    )

    declared_triggers = _string_list(
        context.get("triggers"),
        "context.triggers",
        allow_empty=True,
    )
    effective_context = {
        "release_phase": context.get("release_phase"),
        "triggers": sorted(
            set(declared_triggers) | set(derivation.get("derived_triggers", []))
        ),
        "available_baselines": sorted(
            set(
                _string_list(
                    context.get("available_baselines"),
                    "context.available_baselines",
                    allow_empty=True,
                )
            )
        ),
    }
    cadence_selection = None
    status = "BLOCKED"
    if derivation.get("status") == "DERIVED":
        cadence_selection = select_release_evidence(
            effective_context,
            contract=selected,
        )
        status = str(cadence_selection.get("status") or "BLOCKED")

    receipt: dict[str, Any] = {
        "meta": {
            "kind": CADENCE_RECEIPT_KIND,
            "version": CADENCE_RECEIPT_VERSION,
        },
        "status": status,
        "declared_context": {
            "release_phase": context.get("release_phase"),
            "triggers": sorted(set(declared_triggers)),
            "available_baselines": sorted(
                set(effective_context["available_baselines"])
            ),
        },
        "effective_context": effective_context,
        "installation_trigger_derivation": derivation,
        "cadence_selection": cadence_selection,
        "contract_identity": release_evidence_cadence_contract_identity(selected),
        "authority": (
            "cadence_selection_only_no_baseline_acceptance_execution_human_seal_"
            "release_or_publication_authority"
        ),
        "baseline_accepted": False,
        "publication_authority": False,
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    return receipt


def verify_release_evidence_cadence_receipt(
    receipt: Mapping[str, Any] | None,
    *,
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify immutable receipt structure and current central contract binding."""

    reasons: list[str] = []
    if not isinstance(receipt, Mapping):
        return {"valid": False, "reasons": ["cadence_receipt_missing"]}
    meta = receipt.get("meta")
    if not isinstance(meta, Mapping):
        reasons.append("cadence_receipt_meta_missing")
    else:
        if meta.get("kind") != CADENCE_RECEIPT_KIND:
            reasons.append("cadence_receipt_kind_mismatch")
        if meta.get("version") != CADENCE_RECEIPT_VERSION:
            reasons.append("cadence_receipt_version_mismatch")
    expected_digest = str(receipt.get("receipt_sha256") or "")
    payload = deepcopy(dict(receipt))
    payload.pop("receipt_sha256", None)
    if expected_digest != _canonical_sha256(payload):
        reasons.append("cadence_receipt_digest_mismatch")
    selected = (
        dict(contract)
        if isinstance(contract, Mapping)
        else load_release_evidence_cadence_contract()
    )
    if receipt.get("contract_identity") != release_evidence_cadence_contract_identity(
        selected
    ):
        reasons.append("cadence_contract_identity_mismatch")
    derivation = receipt.get("installation_trigger_derivation")
    selection = receipt.get("cadence_selection")
    effective_context = receipt.get("effective_context")
    if not isinstance(derivation, Mapping):
        reasons.append("installation_trigger_derivation_missing")
    else:
        recorded_derivation_digest = str(derivation.get("receipt_sha256") or "")
        derivation_payload = deepcopy(dict(derivation))
        derivation_payload.pop("receipt_sha256", None)
        if recorded_derivation_digest != _canonical_sha256(derivation_payload):
            reasons.append("installation_trigger_derivation_digest_mismatch")
    if receipt.get("authority") != (
        "cadence_selection_only_no_baseline_acceptance_execution_human_seal_"
        "release_or_publication_authority"
    ):
        reasons.append("cadence_receipt_authority_mismatch")
    if receipt.get("status") == "BLOCKED":
        if isinstance(derivation, Mapping) and derivation.get("status") != "BLOCKED":
            if not isinstance(selection, Mapping) or selection.get("status") != "BLOCKED":
                reasons.append("cadence_receipt_blocked_state_unexplained")
    else:
        if not isinstance(selection, Mapping):
            reasons.append("cadence_selection_missing")
        elif not isinstance(effective_context, Mapping):
            reasons.append("effective_context_missing")
        else:
            declared_context = receipt.get("declared_context")
            if not isinstance(declared_context, Mapping):
                reasons.append("declared_context_missing")
            elif isinstance(derivation, Mapping):
                expected_effective = {
                    "release_phase": declared_context.get("release_phase"),
                    "triggers": sorted(
                        set(declared_context.get("triggers", []))
                        | set(derivation.get("derived_triggers", []))
                    ),
                    "available_baselines": sorted(
                        set(declared_context.get("available_baselines", []))
                    ),
                }
                if effective_context != expected_effective:
                    reasons.append("derived_triggers_not_consumed_by_effective_context")
            try:
                expected_selection = select_release_evidence(
                    effective_context,
                    contract=selected,
                )
            except (TypeError, ValueError) as exc:
                reasons.append(f"effective_context_invalid:{type(exc).__name__}")
            else:
                if selection != expected_selection:
                    reasons.append("cadence_selection_recomputation_mismatch")
                if receipt.get("status") != expected_selection.get("status"):
                    reasons.append("cadence_receipt_status_mismatch")
    if receipt.get("baseline_accepted") is not False:
        reasons.append("cadence_receipt_must_not_accept_baseline")
    if receipt.get("publication_authority") is not False:
        reasons.append("cadence_receipt_must_not_grant_publication")
    return {"valid": not reasons, "reasons": sorted(set(reasons))}


def validate_release_evidence_cadence_contract(
    contract: Mapping[str, Any],
    *,
    root: Path = ROOT,
) -> None:
    meta = contract.get("meta")
    governance = contract.get("governance")
    validation = contract.get("validation")
    invariants = contract.get("invariants")
    input_contract = contract.get("input_contract")
    evidence_identity = contract.get("evidence_identity")
    if not all(
        isinstance(value, Mapping)
        for value in (
            meta,
            governance,
            validation,
            invariants,
            input_contract,
            evidence_identity,
        )
    ):
        raise ValueError("Release evidence cadence contract is missing required object sections")
    if meta.get("kind") != "nexora.release_evidence_cadence_contract":
        raise ValueError("Release evidence cadence contract kind is invalid")
    if meta.get("status") != "normative":
        raise ValueError("Release evidence cadence contract must be normative")
    if governance.get("decision_authority") != "evidence_cadence_only":
        raise ValueError("Cadence policy must not grant execution authority")
    if governance.get("publication_authority") != "never_inferred":
        raise ValueError("Publication authority must never be inferred")

    required_cadences = set(
        _string_list(
            validation.get("required_cadence_classes"),
            "validation.required_cadence_classes",
        )
    )
    if required_cadences != SUPPORTED_CADENCES:
        raise ValueError(
            "Cadence classes must exactly match the selector's five supported algorithms"
        )
    required_aggregate_strategies = set(
        _string_list(
            validation.get("required_aggregate_strategies"),
            "validation.required_aggregate_strategies",
        )
    )
    if required_aggregate_strategies != SUPPORTED_AGGREGATE_STRATEGIES:
        raise ValueError(
            "Aggregate strategies must exactly match the selector's supported algorithms"
        )
    for field in (
        "unknown_cadence_behavior",
        "unknown_trigger_behavior",
        "unknown_owner_behavior",
        "unknown_release_phase_behavior",
    ):
        if validation.get(field) != "block":
            raise ValueError(f"{field} must fail closed")

    required_context_fields = set(
        _string_list(input_contract.get("required_fields"), "input_contract.required_fields")
    )
    if required_context_fields != REQUIRED_CONTEXT_FIELDS:
        raise ValueError("Input contract does not match the selector context")
    release_phases = set(
        _string_list(input_contract.get("release_phases"), "input_contract.release_phases")
    )
    phase_authority = input_contract.get("release_phase_authority")
    if not isinstance(phase_authority, Mapping):
        raise ValueError("input_contract.release_phase_authority must be an object")
    if (
        phase_authority.get("source")
        != "config/sage_self_development_validation_profiles.json"
        or phase_authority.get("json_pointer")
        != "/input_contract/allowed_release_phases"
        or phase_authority.get("mapping_mode") != "exact_shared_vocabulary"
    ):
        raise ValueError("Release phases must bind to the canonical self-development phase owner")
    phase_owner = load_json_object_strict_cached(
        root / str(phase_authority["source"]),
        label="SAGE self-development validation profiles",
    )
    canonical_phases = set(
        _string_list(
            (phase_owner.get("input_contract") or {}).get("allowed_release_phases"),
            "sage_self_development_validation_profiles.input_contract.allowed_release_phases",
        )
    )
    if release_phases != canonical_phases:
        raise ValueError(
            "Release evidence phases must exactly match the canonical self-development phases"
        )
    trigger_catalog = input_contract.get("trigger_catalog")
    if not isinstance(trigger_catalog, list) or not trigger_catalog:
        raise ValueError("input_contract.trigger_catalog must be a non-empty list")
    trigger_ids: set[str] = set()
    for row in trigger_catalog:
        if not isinstance(row, Mapping):
            raise ValueError("Trigger catalog entries must be objects")
        trigger_id = str(row.get("id") or "").strip()
        trigger_class = str(row.get("class") or "").strip()
        if not trigger_id or not trigger_class or trigger_id in trigger_ids:
            raise ValueError("Trigger ids and classes must be non-empty and ids must be unique")
        trigger_ids.add(trigger_id)

    installation_derivation = contract.get("installation_trigger_derivation")
    if not isinstance(installation_derivation, Mapping):
        raise ValueError("installation_trigger_derivation must be an object")
    if (
        installation_derivation.get("schema")
        != "v1_content_bound_semantic_ownership"
        or installation_derivation.get("authority")
        != "central_source_cli_installation_and_runtime_owners"
        or installation_derivation.get("root_entrypoint_role") != "root_entrypoint"
        or installation_derivation.get("transitive_local_python_imports_for_proof_steps")
        is not True
        or installation_derivation.get("unknown_source_layer_behavior") != "block"
        or installation_derivation.get("generated_runtime_change_behavior") != "block"
        or installation_derivation.get("known_unmatched_path_behavior") != "unaffected"
        or installation_derivation.get("receipt_authority")
        != "cadence_input_only_no_baseline_acceptance_or_publication_authority"
    ):
        raise ValueError("Installation trigger derivation must preserve fail-closed authority")

    derivation_triggers: set[str] = set()
    for field in ("source_role_contract", "proof_steps_contract"):
        relative = _normalized_repository_relative_path(installation_derivation.get(field))
        if relative is None or not (root / relative).is_file():
            raise ValueError(f"Installation trigger authority path is unsafe or missing: {field}")
    observed_derivation_identities: dict[str, set[str]] = {}
    for collection_name in ("proof_steps", "exact_authority_paths"):
        rows = installation_derivation.get(collection_name)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"installation_trigger_derivation.{collection_name} must be non-empty")
        seen: set[str] = set()
        key = "id" if collection_name == "proof_steps" else "path"
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError(f"Invalid installation trigger row: {collection_name}")
            identity = str(row.get(key) or "").strip()
            trigger = str(row.get("trigger") or "").strip()
            if not identity or identity in seen:
                raise ValueError(f"Installation trigger identities must be unique: {collection_name}")
            if trigger not in trigger_ids:
                raise ValueError(f"Unknown installation derivation trigger: {trigger}")
            if collection_name == "exact_authority_paths":
                relative = _normalized_repository_relative_path(identity)
                if relative is None or not (root / relative).is_file():
                    raise ValueError(f"Installation authority source is unsafe or missing: {identity}")
            seen.add(identity)
            derivation_triggers.add(trigger)
        observed_derivation_identities[collection_name] = seen
    if observed_derivation_identities.get("proof_steps") != set(
        INSTALLATION_DERIVATION_REQUIRED_PROOF_STEPS
    ):
        raise ValueError("Installation proof-step semantic owners cannot be narrowed or widened implicitly")
    if not INSTALLATION_DERIVATION_REQUIRED_AUTHORITY_PATHS.issubset(
        observed_derivation_identities.get("exact_authority_paths", set())
    ):
        raise ValueError("Installation exact authority hard floor is incomplete")

    layer_trigger_rows = installation_derivation.get("source_layer_triggers")
    if not isinstance(layer_trigger_rows, Mapping) or not layer_trigger_rows:
        raise ValueError("Installation source-layer trigger mapping is missing")
    taxonomy = _load_contract_at(
        root,
        "config/source_layer_taxonomy.json",
        label="source layer taxonomy",
    )
    known_layers = {
        str(row.get("id"))
        for row in taxonomy.get("layers", [])
        if isinstance(row, Mapping) and str(row.get("id") or "")
    }
    if set(map(str, layer_trigger_rows)) - known_layers:
        raise ValueError("Installation source-layer mapping references unknown layers")
    if any(
        layer_trigger_rows.get(layer) != trigger
        for layer, trigger in INSTALLATION_DERIVATION_REQUIRED_LAYER_TRIGGERS.items()
    ):
        raise ValueError("Installation source-layer trigger hard floor is incomplete")
    for trigger in layer_trigger_rows.values():
        if str(trigger) not in trigger_ids:
            raise ValueError(f"Unknown installation source-layer trigger: {trigger}")
        derivation_triggers.add(str(trigger))

    metadata_rows = installation_derivation.get("semantic_metadata")
    if not isinstance(metadata_rows, list) or not metadata_rows:
        raise ValueError("Installation semantic metadata rules are missing")
    metadata_paths: set[str] = set()
    for row in metadata_rows:
        if not isinstance(row, Mapping):
            raise ValueError("Installation semantic metadata rows must be objects")
        relative = _normalized_repository_relative_path(row.get("path"))
        ignored = row.get("ignored_json_paths")
        changed_triggers = row.get("changed_triggers")
        if (
            relative is None
            or relative in metadata_paths
            or not (root / relative).is_file()
            or not isinstance(ignored, list)
            or not ignored
            or not all(isinstance(value, str) and value.strip() for value in ignored)
            or not isinstance(changed_triggers, list)
            or not changed_triggers
        ):
            raise ValueError("Installation semantic metadata rule is invalid")
        unknown = sorted(set(map(str, changed_triggers)) - trigger_ids)
        if unknown:
            raise ValueError(f"Unknown installation semantic metadata triggers: {unknown}")
        metadata_paths.add(relative)
        derivation_triggers.update(map(str, changed_triggers))
    pyproject_rules = [
        row
        for row in metadata_rows
        if isinstance(row, Mapping) and row.get("path") == "pyproject.toml"
    ]
    if (
        len(pyproject_rules) != 1
        or set(pyproject_rules[0].get("ignored_json_paths", [])) != {"project.version"}
        or set(pyproject_rules[0].get("changed_triggers", []))
        != {"dependency_semantics_changed", "runtime_support_changed"}
    ):
        raise ValueError("Pyproject installation semantic hard floor is incomplete")

    installation_closure = _installation_trigger_closure(contract, root=root)
    if installation_closure.get("status") != "COMPLETE":
        raise ValueError(
            "Installation semantic owner closure is incomplete: "
            + ", ".join(installation_closure.get("reasons") or [])
        )

    identity_dimensions = set(
        _string_list(
            evidence_identity.get("dimensions"),
            "evidence_identity.dimensions",
        )
    )
    allowed_boundaries = set(
        _string_list(
            validation.get("allowed_owner_boundaries"),
            "validation.allowed_owner_boundaries",
        )
    )
    required_owner_fields = set(
        _string_list(
            validation.get("required_owner_fields"),
            "validation.required_owner_fields",
        )
    )
    owners = contract.get("owners")
    if not isinstance(owners, list) or not owners:
        raise ValueError("owners must be a non-empty list")
    owner_ids: set[str] = set()
    publisher_ids: list[str] = []
    for owner in owners:
        if not isinstance(owner, Mapping):
            raise ValueError("Owner entries must be objects")
        missing = sorted(required_owner_fields - set(owner))
        if missing:
            raise ValueError(f"Owner is missing required fields: {missing}")
        owner_id = str(owner.get("id") or "").strip()
        boundary = str(owner.get("boundary") or "").strip()
        if not owner_id or owner_id in owner_ids:
            raise ValueError("Owner ids must be unique and non-empty")
        if boundary not in allowed_boundaries:
            raise ValueError(f"Unknown owner boundary for {owner_id}: {boundary}")
        if not str(owner.get("authority") or "").strip():
            raise ValueError(f"Owner authority is missing: {owner_id}")
        sources = _string_list(owner.get("sources"), f"owners.{owner_id}.sources")
        may_publish = owner.get("may_publish")
        if not isinstance(may_publish, bool):
            raise ValueError(f"Owner may_publish must be boolean: {owner_id}")
        if boundary == "source_repository":
            invalid_sources = [
                source
                for source in sources
                if source.startswith("private://")
                or not _safe_repository_source(root, source)
            ]
            if invalid_sources:
                raise ValueError(
                    f"Source-repository owner has unsafe, missing or private sources: "
                    f"{owner_id}={invalid_sources}"
                )
        elif any(not _safe_private_source(source) for source in sources):
            raise ValueError(
                f"Private-controller owner sources must use safe private:// references: {owner_id}"
            )
        if may_publish:
            publisher_ids.append(owner_id)
        owner_ids.add(owner_id)

    publisher_owner_id = str(governance.get("one_publisher_owner_id") or "").strip()
    if publisher_ids != [publisher_owner_id]:
        raise ValueError(
            "Exactly the existing private release controller must own publication"
        )

    required_subjects = set(
        _string_list(validation.get("required_subjects"), "validation.required_subjects")
    )
    required_subject_cadences = validation.get("required_subject_cadences")
    if not isinstance(required_subject_cadences, Mapping):
        raise ValueError("validation.required_subject_cadences must be an object")
    if set(required_subject_cadences) != required_subjects:
        raise ValueError("Required subject cadence keys must match required subjects")

    evidence_rows = contract.get("evidence")
    if not isinstance(evidence_rows, list) or not evidence_rows:
        raise ValueError("evidence must be a non-empty list")
    evidence_ids: set[str] = set()
    evidence_by_id: dict[str, Mapping[str, Any]] = {}
    observed_subject_cadences: dict[str, set[str]] = {
        subject: set() for subject in required_subjects
    }
    observed_cadences: set[str] = set()
    observed_aggregate_strategies: set[str] = set()
    for row in evidence_rows:
        if not isinstance(row, Mapping):
            raise ValueError("Evidence entries must be objects")
        evidence_id = str(row.get("id") or "").strip()
        subject = str(row.get("subject") or "").strip()
        cadence = str(row.get("cadence") or "").strip()
        owner_id = str(row.get("owner_id") or "").strip()
        if not evidence_id or evidence_id in evidence_ids:
            raise ValueError("Evidence ids must be unique and non-empty")
        if subject not in required_subjects:
            raise ValueError(f"Unknown evidence subject: {evidence_id}={subject}")
        if cadence not in required_cadences:
            raise ValueError(f"Unknown evidence cadence: {evidence_id}={cadence}")
        aggregate_strategy = str(row.get("aggregate_strategy") or "").strip()
        if cadence == "final_candidate_aggregate":
            if aggregate_strategy not in required_aggregate_strategies:
                raise ValueError(
                    f"Final aggregate evidence has unknown aggregate strategy: "
                    f"{evidence_id}={aggregate_strategy or '<missing>'}"
                )
            observed_aggregate_strategies.add(aggregate_strategy)
        elif aggregate_strategy:
            raise ValueError(
                f"Only final candidate aggregate evidence may declare aggregate_strategy: "
                f"{evidence_id}"
            )
        if owner_id not in owner_ids:
            raise ValueError(f"Unknown evidence owner: {evidence_id}={owner_id}")
        phases = set(
            _string_list(
                row.get("required_phases"),
                f"evidence.{evidence_id}.required_phases",
                allow_empty=True,
            )
        )
        unknown_phases = sorted(phases - release_phases)
        if unknown_phases:
            raise ValueError(f"Unknown release phases for {evidence_id}: {unknown_phases}")
        triggers = set(
            _string_list(
                row.get("run_triggers"),
                f"evidence.{evidence_id}.run_triggers",
                allow_empty=True,
            )
        )
        unknown_triggers = sorted(triggers - trigger_ids)
        if unknown_triggers:
            raise ValueError(f"Unknown triggers for {evidence_id}: {unknown_triggers}")
        dimensions = set(
            _string_list(
                row.get("identity_dimensions"),
                f"evidence.{evidence_id}.identity_dimensions",
            )
        )
        unknown_dimensions = sorted(dimensions - identity_dimensions)
        if unknown_dimensions:
            raise ValueError(
                f"Unknown identity dimensions for {evidence_id}: {unknown_dimensions}"
            )
        if not str(row.get("rule") or "").strip():
            raise ValueError(f"Evidence rule is missing: {evidence_id}")
        if cadence == "baseline_plus_delta":
            fresh_triggers = set(
                _string_list(
                    row.get("fresh_retest_triggers"),
                    f"evidence.{evidence_id}.fresh_retest_triggers",
                )
            )
            if not fresh_triggers.issubset(triggers):
                raise ValueError(
                    f"Fresh retest triggers must be declared run triggers: {evidence_id}"
                )
            if fresh_triggers - trigger_ids:
                raise ValueError(f"Unknown fresh retest trigger: {evidence_id}")
            if row.get("baseline_required") is not True:
                raise ValueError(f"Baseline-plus-delta evidence must require a baseline: {evidence_id}")
        elif "fresh_retest_triggers" in row or "baseline_required" in row:
            raise ValueError(
                f"Only baseline-plus-delta evidence may declare baseline fields: {evidence_id}"
            )
        evidence_ids.add(evidence_id)
        evidence_by_id[evidence_id] = row
        observed_cadences.add(cadence)
        observed_subject_cadences[subject].add(cadence)

    if observed_cadences != required_cadences:
        raise ValueError("Every required cadence class must be exercised by evidence")
    if observed_aggregate_strategies != required_aggregate_strategies:
        raise ValueError("Every required aggregate strategy must be exercised by evidence")
    for subject, expected in required_subject_cadences.items():
        expected_cadences = set(
            _string_list(expected, f"validation.required_subject_cadences.{subject}")
        )
        if not expected_cadences.issubset(observed_subject_cadences[subject]):
            raise ValueError(
                f"Evidence subject is missing required cadence: {subject}="
                f"{sorted(expected_cadences - observed_subject_cadences[subject])}"
            )

    install_id = str(validation.get("independent_install_evidence_id") or "").strip()
    version_trigger = str(validation.get("version_only_trigger_id") or "").strip()
    install = evidence_by_id.get(install_id)
    if not isinstance(install, Mapping) or install.get("cadence") != "baseline_plus_delta":
        raise ValueError("Independent physical install must use baseline_plus_delta")
    required_retest = set(
        _string_list(
            validation.get("required_independent_retest_triggers"),
            "validation.required_independent_retest_triggers",
        )
    )
    fresh_retest = set(install.get("fresh_retest_triggers", []))
    if not required_retest.issubset(fresh_retest):
        raise ValueError("Independent install is missing protected semantic retest triggers")
    if version_trigger not in trigger_ids:
        raise ValueError("Version-only trigger is not declared")
    if version_trigger in fresh_retest:
        raise ValueError("Version identity alone must not force independent physical retest")
    if not derivation_triggers.issubset(fresh_retest):
        raise ValueError("Derived installation triggers must force independent physical retest")

    work_item_ids = set(
        _string_list(governance.get("work_item_ids"), "governance.work_item_ids")
    )
    required_work_items = set(
        _string_list(
            validation.get("required_work_item_ids"),
            "validation.required_work_item_ids",
        )
    )
    if not required_work_items.issubset(work_item_ids):
        raise ValueError("Cadence contract is not owned by the required v1.1.1 work items")

    if (
        invariants.get("version_identity_change_alone_requires_independent_physical_retest")
        is not False
        or invariants.get("independent_physical_retest_requires_protected_semantic_trigger")
        is not True
        or invariants.get("missing_required_baseline_behavior") != "block"
        or invariants.get("failed_stale_tampered_or_ambiguous_evidence_reuse")
        != "forbidden"
        or invariants.get("machine_evidence_may_issue_human_authority") is not False
        or invariants.get("final_candidate_aggregate_reuse")
        != "content_bound_current_receipts_and_exact_frozen_candidate_identity_only"
        or invariants.get("cadence_decision_may_execute_publication") is not False
        or invariants.get("release_version_is_not_an_evidence_identity") is not True
    ):
        raise ValueError("Release evidence invariants must preserve fail-closed authority")

    acceptance_cases = contract.get("acceptance_cases")
    if not isinstance(acceptance_cases, list) or not acceptance_cases:
        raise ValueError("acceptance_cases must be a non-empty list")
    case_ids: set[str] = set()
    for case in acceptance_cases:
        if not isinstance(case, Mapping):
            raise ValueError("Acceptance cases must be objects")
        case_id = str(case.get("id") or "").strip()
        expected = case.get("expected")
        if not case_id or case_id in case_ids or not isinstance(case.get("context"), Mapping):
            raise ValueError("Acceptance case ids must be unique and contexts must be objects")
        if not isinstance(expected, Mapping) or not expected:
            raise ValueError(f"Acceptance case expected decisions are missing: {case_id}")
        unknown_evidence = sorted(set(expected) - evidence_ids)
        unknown_dispositions = sorted(set(expected.values()) - SUPPORTED_DISPOSITIONS)
        if unknown_evidence or unknown_dispositions:
            raise ValueError(
                f"Acceptance case references unknown decisions: {case_id} "
                f"evidence={unknown_evidence} dispositions={unknown_dispositions}"
            )
        case_ids.add(case_id)


def _normalize_context(
    context: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> tuple[str, set[str], set[str]]:
    missing = sorted(REQUIRED_CONTEXT_FIELDS - set(context))
    if missing:
        raise ValueError(f"Release evidence context missing fields: {missing}")
    input_contract = contract["input_contract"]
    phase = context.get("release_phase")
    if not isinstance(phase, str) or phase not in input_contract["release_phases"]:
        raise ValueError(f"Unknown release phase: {phase}")
    triggers = set(_string_list(context.get("triggers"), "context.triggers", allow_empty=True))
    known_triggers = {str(row["id"]) for row in input_contract["trigger_catalog"]}
    unknown_triggers = sorted(triggers - known_triggers)
    if unknown_triggers:
        raise ValueError(f"Unknown release evidence triggers: {unknown_triggers}")
    baselines = set(
        _string_list(
            context.get("available_baselines"),
            "context.available_baselines",
            allow_empty=True,
        )
    )
    evidence_ids = {str(row["id"]) for row in contract["evidence"]}
    unknown_baselines = sorted(baselines - evidence_ids)
    if unknown_baselines:
        raise ValueError(f"Unknown evidence baselines: {unknown_baselines}")
    return phase, triggers, baselines


def select_release_evidence(
    context: Mapping[str, Any],
    *,
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    selected = dict(contract) if contract is not None else load_release_evidence_cadence_contract()
    validate_release_evidence_cadence_contract(selected)
    phase, triggers, baselines = _normalize_context(context, selected)

    decisions: dict[str, dict[str, Any]] = {}
    blocked: list[str] = []
    for row in selected["evidence"]:
        evidence_id = str(row["id"])
        cadence = str(row["cadence"])
        required_phases = set(row["required_phases"])
        declared_triggers = set(row["run_triggers"])
        matched_triggers = sorted(triggers & declared_triggers)
        phase_required = phase in required_phases

        disposition = "not_required"
        reason = "no_declared_phase_or_trigger_requires_current_evidence"
        if cadence == "always_fresh":
            if phase_required or matched_triggers:
                disposition = "run_fresh"
                reason = "always_fresh_at_declared_phase_or_trigger"
        elif cadence == "change_triggered":
            if matched_triggers:
                disposition = "run_fresh"
                reason = "declared_semantic_change_trigger"
        elif cadence == "baseline_plus_delta":
            if phase_required:
                fresh_retest_triggers = set(row["fresh_retest_triggers"])
                if triggers & fresh_retest_triggers:
                    disposition = "run_fresh"
                    reason = "protected_installation_semantics_or_environment_changed"
                elif evidence_id in baselines:
                    disposition = "baseline_plus_delta"
                    reason = "accepted_baseline_with_current_candidate_delta"
                else:
                    disposition = "blocked_missing_baseline"
                    reason = "required_baseline_is_missing"
                    blocked.append(evidence_id)
        elif cadence == "periodic":
            if matched_triggers:
                disposition = "run_fresh"
                reason = "declared_periodic_window_due"
        elif cadence == "final_candidate_aggregate":
            if phase_required:
                disposition = "run_fresh"
                reason = "exact_frozen_candidate_aggregate"
        else:
            raise ValueError(f"Unsupported release evidence cadence: {cadence}")

        decisions[evidence_id] = {
            "subject": row["subject"],
            "cadence": cadence,
            "owner_id": row["owner_id"],
            "required": disposition != "not_required",
            "disposition": disposition,
            "reason": reason,
            "matched_triggers": matched_triggers,
            "aggregate_strategy": (
                row.get("aggregate_strategy")
                if cadence == "final_candidate_aggregate"
                else None
            ),
            "aggregate_action": (
                row.get("aggregate_strategy")
                if cadence == "final_candidate_aggregate"
                and disposition == "run_fresh"
                else "not_applicable"
            ),
        }

    return {
        "status": "BLOCKED" if blocked else "SELECTED",
        "release_phase": phase,
        "triggers": sorted(triggers),
        "available_baselines": sorted(baselines),
        "decisions": decisions,
        "blocked_evidence": blocked,
        "authority": "evidence_cadence_only_no_execution_or_publication_authority",
    }
