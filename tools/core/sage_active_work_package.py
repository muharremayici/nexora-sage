"""Read and atomically transition the SAGE developer work-package ledger."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, save_json_atomic
from tools.core.json_io import load_json_object_strict
from tools.core.source_layer_classifier import classify_source_layer
from tools.core.work_package_state import (
    _closure_contract,
    _inherited_dirty_source_path,
    inherited_dirty_file_identity,
    load_ledger,
    package_closure_is_complete,
    package_from_ledger,
    successor_matches_selection,
)


LEDGER_PATH = CONFIG_DIR / "sage_active_work_package.json"
LOOP_CONTRACT_PATH = CONFIG_DIR / "sage_development_loop_contract.json"
TRACE_CONTRACT_PATH = CONFIG_DIR / "governance_trace_contract.json"


def capture_inherited_dirty_baseline(
    successor: dict[str, Any],
    changed_files: list[str],
    *,
    root: Path = CODE_MAPS_DIR,
    trace_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record exact bytes of pre-existing governed dirt outside the next package."""
    contract = trace_contract if trace_contract is not None else load_json_object_strict(
        TRACE_CONTRACT_PATH, label="Governance trace contract"
    )
    closeout = contract["work_package_receipts"]["closeout_policy"]
    scope = closeout["changed_file_scope"]
    excluded = set(scope["excluded_source_layers"])
    unknown = set(scope["unknown_source_layers"])
    declared = {str(item).replace("\\", "/") for item in successor.get("affected_contracts", [])}
    files: dict[str, str] = {}
    for raw in sorted({str(item) for item in changed_files if str(item)}):
        if raw in declared:
            continue
        source = _inherited_dirty_source_path(raw, root)
        layer, _ = classify_source_layer(source.resolve(), root=root.resolve())
        if layer in unknown:
            raise ValueError(f"Unknown inherited dirty source layer: {raw}")
        if layer not in excluded:
            files[raw] = inherited_dirty_file_identity(raw, root=root)
    policy = closeout["inherited_dirty_baseline"]
    return {"version": policy["version"], "status": policy["captured_status"], "files": files}


def load_active_work_package_ledger() -> dict[str, Any]:
    return load_ledger(LEDGER_PATH)


def active_work_package() -> dict[str, Any]:
    return package_from_ledger(load_active_work_package_ledger())


def package_history(ledger: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    current = ledger if ledger is not None else load_active_work_package_ledger()
    rows = current.get("package_history")
    if not isinstance(rows, list):
        raise ValueError("SAGE active work package ledger must contain package_history")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("SAGE package_history must contain only objects")
    return rows


def _stable_package_identity(package: dict[str, Any]) -> dict[str, Any]:
    release_scope = package.get("release_scope") if isinstance(package.get("release_scope"), dict) else {}
    return {
        "id": package.get("id"),
        "execution_wave": package.get("execution_wave"),
        "work_item_ids": [str(item) for item in package.get("work_item_ids", []) if str(item)],
        "objective": package.get("objective"),
        "affected_contracts": [
            str(item)
            for item in package.get("affected_contracts", [])
            if str(item)
        ],
        "release_scope": deepcopy(release_scope),
    }


def build_package_transition(
    ledger: dict[str, Any],
    successor: dict[str, Any],
    *,
    successor_selection: dict[str, Any],
    closed_package: dict[str, Any] | None = None,
    closure_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Close-and-append the current package while activating one successor."""
    current = ledger.get("active_package")
    history = ledger.get("package_history")
    if not isinstance(current, dict) or not isinstance(history, list):
        raise ValueError("Package transition requires active_package and package_history")
    if any(not isinstance(row, dict) for row in history):
        raise ValueError("Package history must contain only objects")
    contract = closure_contract if closure_contract is not None else _closure_contract()
    snapshot = closed_package if closed_package is not None else current
    if not isinstance(snapshot, dict):
        raise ValueError("Closed package snapshot must be an object")
    if closed_package is not None and _stable_package_identity(snapshot) != _stable_package_identity(current):
        raise ValueError("Closed package snapshot must preserve the active package identity and scope")
    if current.get("status") == "closed" and snapshot != current:
        raise ValueError("Already closed package history must remain unchanged")

    current_id = str(current.get("id") or "").strip()
    snapshot_id = str(snapshot.get("id") or "").strip()
    successor_id = str(successor.get("id") or "").strip()
    history_ids = [str(row.get("id") or "").strip() for row in history]
    if not current_id or snapshot_id != current_id or not successor_id or current_id == successor_id:
        raise ValueError("Package transition requires distinct stable non-empty package identities")
    if not package_closure_is_complete(snapshot, contract):
        raise ValueError("Current package closure evidence must be complete before transition")
    if successor.get("status") != "in_progress" or (successor.get("closure") or {}).get("status") != "in_progress":
        raise ValueError("Successor package must start in progress")
    if any(not package_closure_is_complete(row, contract) for row in history):
        raise ValueError("Existing package history contains incomplete closure evidence")
    if len(history_ids) != len(set(history_ids)) or current_id in history_ids or successor_id in history_ids:
        raise ValueError("Package transition would create duplicate history")
    if not successor_matches_selection(successor, successor_selection):
        raise ValueError(
            "Successor package must match one exact transition-eligible registry projection"
        )

    updated = deepcopy(ledger)
    updated["package_history"].append(deepcopy(snapshot))
    updated["active_package"] = deepcopy(successor)
    return updated


def save_package_transition(
    successor: dict[str, Any],
    *,
    successor_selection: dict[str, Any],
    closed_package: dict[str, Any] | None = None,
    closure_contract: dict[str, Any] | None = None,
    ledger_path: Path = LEDGER_PATH,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    """Persist close-and-activate as one shared atomic JSON write."""
    ledger = load_json_object_strict(ledger_path, label="SAGE active work package ledger")
    updated = build_package_transition(
        ledger,
        successor,
        successor_selection=successor_selection,
        closed_package=closed_package,
        closure_contract=closure_contract,
    )
    if "inherited_dirty_baseline" in successor:
        raise ValueError("Successor cannot supply its own inherited dirty baseline")
    if changed_files is None:
        # Reuse the same Git discovery authority as the closeout proposal.
        from tools.core.artifact_store import get_adaptive_timeout
        from tools.generate_semantic_diff_lesson_projection import changed_files_from_git

        loop = load_json_object_strict(LOOP_CONTRACT_PATH, label="SAGE development loop contract")
        timeout = int(
            loop["semantic_diff_review"]["lesson_application_levels"]["impacted"]
            ["projection_contract"]["git_discovery_timeout_seconds"]
        )
        changed_files = changed_files_from_git(timeout_seconds=get_adaptive_timeout(timeout))
    # The non-mutating closeout proposal is advisory to humans, but a BLOCKED
    # live proposal cannot be turned into a closed package by changing adapters.
    from tools.core.work_package_receipts import (
        build_closed_package_activation_proposal,
        build_published_package_activation_proposal,
        build_work_package_closeout_proposal,
        propose_work_package_evidence,
    )
    from tools.validate_sage_work_package_closure import run as validate_closure

    receipt_projection = propose_work_package_evidence()
    closeout = build_work_package_closeout_proposal(
        package=ledger["active_package"],
        receipt_projection=receipt_projection,
        closure_validation=validate_closure(),
        changed_files=changed_files,
        package_history=ledger["package_history"],
    )
    trace_contract = load_json_object_strict(
        TRACE_CONTRACT_PATH, label="Governance trace contract"
    )
    receipt_contract = trace_contract["work_package_receipts"]
    closeout_policy = receipt_contract["closeout_policy"]
    ready_statuses = {
        closeout_policy["transition_required_status"],
        closeout_policy["accepted_risk_transition_status"],
    }
    if ledger["active_package"].get("status") == "closed":
        activation = build_closed_package_activation_proposal(
            package=ledger["active_package"],
            successor=successor,
            successor_selection=successor_selection,
            receipt_projection=receipt_projection,
            closeout=closeout,
            closure_contract=closure_contract,
        )
        activation_policy = receipt_contract.get("closed_package_activation", {})
        if not activation.get("ready") or activation["status"] != activation_policy.get("ready_status"):
            raise ValueError(f"Closed package activation is blocked: {activation['errors']}")
    elif "published_completion_reference" in ledger["active_package"]:
        activation = build_published_package_activation_proposal(
            package=ledger["active_package"], successor=successor,
            successor_selection=successor_selection,
            receipt_projection=receipt_projection, closeout=closeout,
        )
        if not activation.get("ready"):
            raise ValueError(f"Published package activation is blocked: {activation['errors']}")
    elif closeout["status"] not in ready_statuses:
        raise ValueError("Current work package closeout proposal is blocked")
    live_diff = closeout.get("live_diff")
    accepted_unknown = (
        live_diff.get("legacy_accepted_unknown_changed_files", [])
        if isinstance(live_diff, dict) else []
    )
    if (
        closeout["status"] == closeout_policy["accepted_risk_transition_status"]
        and not accepted_unknown
    ):
        raise ValueError("Accepted-risk closeout has no exact legacy dirty file set")
    if accepted_unknown:
        expected = closeout_policy["one_time_legacy_exception"]["current_file_sha256"]
        if any(
            inherited_dirty_file_identity(relative) != expected[relative]
            for relative in accepted_unknown
        ):
            raise ValueError("Accepted legacy dirty bytes changed before transition")
    updated["active_package"]["inherited_dirty_baseline"] = capture_inherited_dirty_baseline(
        successor, changed_files, trace_contract=trace_contract
    )
    publication_reference = ledger["active_package"].get("published_completion_reference")
    if publication_reference is not None and (
        inherited_dirty_file_identity(publication_reference["path"], root=CODE_MAPS_DIR)
        != publication_reference["sha256"]
    ):
        raise ValueError("Publication completion changed before transition")
    if load_json_object_strict(ledger_path, label="SAGE active work package ledger") != ledger:
        raise ValueError("Work package ledger changed during transition")
    save_json_atomic(ledger_path, updated)
    return updated
