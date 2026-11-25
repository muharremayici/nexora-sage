from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PROFILE_PATH = ROOT / "config" / "distribution_test_profiles.json"
PUBLIC_MANIFEST = ROOT / "PUBLIC_DISTRIBUTION_MANIFEST.json"
def _selection_owner():
    # Keep explicit external receipt writing usable without the source package.
    from tools.core import distribution_test_selection
    return distribution_test_selection


def __getattr__(name):
    if name in {"KNOWN_CLASSIFICATIONS", "KNOWN_PROFILES"}:
        return getattr(_selection_owner(), name)
    raise AttributeError(name)


def _load_profile() -> dict[str, Any]:
    payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Distribution test profile must be a JSON object")
    return payload


def _canonical_classification(payload: dict[str, Any], *, root: Path | None = None) -> tuple[dict[str, str], str]:
    return _selection_owner()._canonical_classification(payload, root=ROOT if root is None else root)


def validate_canonical_distribution_test_inventory() -> dict[str, str]:
    """Fail closed unless every canonical test has one authority classification."""
    classified, _ = _canonical_classification(_load_profile())
    return classified


def _test_token(path: str) -> str:
    return _selection_owner()._test_token(path)


def _partition_contract(payload: dict[str, Any]) -> tuple[list[str], dict[str, str], dict[str, set[str]]]:
    return _selection_owner()._partition_contract(payload)


def _validate_partition_inventory(payload: dict[str, Any], test_paths: list[str]) -> None:
    return _selection_owner()._validate_partition_inventory(payload, test_paths)


def _partition_selected_tests(
    payload: dict[str, Any], test_paths: list[str], profile_name: str,
) -> dict[str, list[str]]:
    return _selection_owner()._partition_selected_tests(payload, test_paths, profile_name)


def _projected_public_tests(payload: dict[str, Any]) -> tuple[list[str], str]:
    return _selection_owner()._projected_public_tests(payload, root=ROOT)


def _resolve_tests_from_payload(
    payload: dict[str, Any], profile_name: str, *, root: Path | None = None
) -> tuple[list[str], str | None]:
    return _selection_owner()._resolve_tests_from_payload(
        payload, profile_name, root=ROOT if root is None else root, alternate_root=root is not None)


def resolve_tests(profile_name: str) -> tuple[list[str], str | None]:
    return _resolve_tests_from_payload(_load_profile(), profile_name)


def _resolve_test_partitions_from_payload(
    payload: dict[str, Any], profile_name: str, *, root: Path | None = None
) -> tuple[dict[str, list[str]], str | None, str]:
    return _selection_owner()._resolve_test_partitions_from_payload(
        payload, profile_name, root=ROOT if root is None else root, alternate_root=root is not None)


def resolve_test_partitions(profile_name: str) -> tuple[dict[str, list[str]], str | None, str]:
    payload = _load_profile()
    return _resolve_test_partitions_from_payload(payload, profile_name)


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _managed_receipt_store(path: Path) -> Any:
    """Reuse the active store only for this installation's managed raw root."""
    resolved = path.resolve()
    if resolved.parent != (ROOT / "output" / ".raw").resolve() or resolved.suffix.lower() != ".json":
        return None
    from tools.core.artifact_store import STORE

    if STORE.raw_path(resolved.stem).resolve() != resolved:
        raise ValueError("Receipt path does not match the active ArtifactStore namespace")
    return STORE


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    store = _managed_receipt_store(path)
    if store is not None:
        # A failed primary commit must propagate; never write a shadow-only PASS.
        store.save_raw(path.stem, payload)
        return
    # Explicit external receipts retain the standalone, dependency-light file path.
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _public_test_init_command(target_root: Path) -> list[str]:
    """Reuse the source-owned setup command without shell expansion or analysis."""
    from tools.core.quality_gate_ci import load_ci_execution_policy

    command = shlex.split(load_ci_execution_policy(ROOT)["commands"]["target_init"])
    if (
        len(command) != 6
        or command[:5] != ["python", "sage.py", "init", "--skip-deps", "--target-root"]
        or not command[5].strip()
        or command[5].startswith("-")
    ):
        raise ValueError("Public test setup requires the declared setup-only target init")
    return [sys.executable, "-B", *command[1:5], str(target_root)]


def _run_pytest(
    tests: list[str],
    *,
    target_fixture: str | None,
    collect_only: bool,
    verbose: bool,
    basetemp: Path | None,
    identity: dict | None = None,
    observation: dict | None = None,
    diagnostic: bool = False,
    preflight_limits: dict | None = None,
    family_parent_context_sha256: str | None = None,
) -> int:
    env = os.environ.copy()
    journal = context = None
    temporary_target: tempfile.TemporaryDirectory[str] | None = None
    temporary_pytest: tempfile.TemporaryDirectory[str] | None = None
    try:
        if target_fixture:
            resolved_fixture = (ROOT / target_fixture).resolve()
            if not resolved_fixture.is_dir():
                raise ValueError(f"target fixture is missing: {target_fixture}")
            try:
                resolved_fixture.relative_to(ROOT)
            except ValueError as exc:
                raise ValueError("target fixture escapes the distribution root") from exc
            temporary_target = tempfile.TemporaryDirectory(
                prefix="nexora-sage-test-target-",
                ignore_cleanup_errors=True,
            )
            external_target = Path(temporary_target.name) / "repository"
            shutil.copytree(resolved_fixture, external_target)
            env["CODEMAPS_TARGET_ROOT"] = str(external_target)

        if PUBLIC_MANIFEST.is_file() and not collect_only:
            if temporary_target is None:
                raise ValueError("Public test setup requires an owned target fixture")
            from tools.core.operational_limits import install_proof_step_timeout_seconds

            # Init is a real test prerequisite, not analysis or borrowed developer config.
            setup = subprocess.run(
                _public_test_init_command(external_target),
                cwd=ROOT,
                env=env,
                check=False,
                timeout=install_proof_step_timeout_seconds("init"),
            )
            if setup.returncode != 0:
                print("[distribution-tests] FAIL: target setup failed; pytest was not started",
                      file=sys.stderr, flush=True)
                return setup.returncode

        if basetemp is None:
            temporary_pytest = tempfile.TemporaryDirectory(
                prefix="nexora-sage-pytest-",
                ignore_cleanup_errors=True,
            )
            resolved_basetemp = Path(temporary_pytest.name)
        else:
            resolved_basetemp = basetemp.resolve()
            resolved_basetemp.parent.mkdir(parents=True, exist_ok=True)
        payload = _load_profile()
        if "public_projection" not in payload:
            from tools.core import distribution_test_execution as execution

            contract = execution.execution_contract(payload)
            env, journal, context = execution.prepare_launch(
                ROOT, tests, env, contract,
                identity=identity if identity is not None else execution.build_execution_identity(ROOT),
                collect_only=collect_only,
                preflight=preflight_limits,
                **({"family_parent_context_sha256": family_parent_context_sha256}
                   if family_parent_context_sha256 is not None else {}),
            )
        command = [sys.executable, "-B", "-m", "pytest", *tests]
        command.extend(
            [
                "-vv" if verbose else "-q",
                "--basetemp",
                str(resolved_basetemp),
                "-p",
                "no:cacheprovider",
            ]
        )
        if collect_only:
            command.append("--collect-only")
        if journal is not None:
            command.extend(["-p", execution.PLUGIN])
            if preflight_limits is not None:
                command.extend(["-c", str(journal.parent / execution.PREFLIGHT_CONFIG_NAME),
                                "--noconftest", "--rootdir", str(ROOT)])
            print(f"[distribution-tests] failure_journal={journal}", flush=True)
        if diagnostic:
            command.extend(["--maxfail=1", "--tb=short"])
        process_observation = None
        if preflight_limits is None:
            returncode = subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
        else:
            from tools.core.release_candidate_preflight import supervise_preflight

            process_observation = supervise_preflight(command, cwd=ROOT, env=env, limits=preflight_limits)
            returncode = process_observation["returncode"]
        if journal is not None:
            result = execution.observe_journal(journal, context, returncode)
            if process_observation is not None:
                result["process"] = process_observation
            if observation is not None:
                observation.update(result)
            if result["status"] != "FINISHED":
                print("[distribution-tests] FAIL: incomplete diagnostic journal", file=sys.stderr, flush=True)
                return returncode or 2
        return returncode
    finally:
        if temporary_pytest is not None:
            temporary_pytest.cleanup()
        if temporary_target is not None:
            temporary_target.cleanup()


def run_candidate_preflight(*, root: Path = ROOT, expected_source: dict | None = None,
                            expected_environment_sha256: str | None = None,
                            parent_dispatch_id: str | None = None,
                            source_only: bool = False,
                            execution_observation: dict | None = None,
                            source_queue_id: str | None = None,
                            max_source_steps: int | None = None) -> dict:
    from tools.core.release_candidate_preflight import SourceReadinessQueue, SourceQueueRefusal, AUTHORITY

    if type(source_only) is not bool:
        raise ValueError("source_readiness_cannot_bind_authoritative_candidate_preflight")
    if not source_only and (source_queue_id is not None or max_source_steps is not None):
        raise ValueError("source_readiness_queue_requires_source_only_mode")
    if max_source_steps is not None and (type(max_source_steps) is not int or max_source_steps < 1):
        raise ValueError("source_readiness_step_limit_must_be_positive")
    if execution_observation is not None and (not source_only or type(execution_observation) is not dict
                                              or execution_observation or source_queue_id is not None
                                              or max_source_steps is not None):
        raise ValueError("source_readiness_execution_observation_requires_empty_source_only_context")
    if source_only and any(value is not None for value in (expected_source, expected_environment_sha256, parent_dispatch_id)):
        raise ValueError("source_readiness_cannot_bind_authoritative_candidate_preflight")
    if root.resolve() != ROOT.resolve():
        raise ValueError("candidate_preflight_installation_owner_mismatch")
    kwargs = dict(root=root, expected_source=expected_source,
                  expected_environment_sha256=expected_environment_sha256,
                  parent_dispatch_id=parent_dispatch_id, source_only=source_only,
                  execution_observation=execution_observation)
    if not source_only:
        return _run_candidate_preflight(**kwargs)
    queue = None
    try:
        queue = SourceReadinessQueue(root, source_queue_id)
        with queue:
            return _run_candidate_preflight(**kwargs, source_queue=queue, max_source_steps=max_source_steps)
    except SourceQueueRefusal as exc:
        result = {"status": "SOURCE_READINESS_RECORDED" if str(exc) == "ALREADY_RECORDED" else "SOURCE_READINESS_BLOCKED",
                  "queue_id": queue.id if queue is not None else None, "queue_state": str(exc),
                  "actual_passed_cases": 0, "authority": AUTHORITY,
                  "canonical_test_step_reuse": False, "publication_authority": False}
        print("[source-readiness] " + json.dumps(result), flush=True)
        return result


def _run_candidate_preflight(*, root: Path, expected_source: dict | None,
                             expected_environment_sha256: str | None, parent_dispatch_id: str | None,
                             source_only: bool, execution_observation: dict | None,
                             source_queue=None, max_source_steps: int | None = None) -> dict:
    from tools.core.release_candidate_preflight import (
        candidate_preflight_plan, source_validator_observation, required_fixture_observation,
        manual_sample_readiness_observation, work_item_readiness_observation, AUTHORITY,
    )
    from tools.core.distribution_test_execution import build_execution_identity
    from tools.core.source_state_identity import source_state_identity

    started = time.monotonic()
    if type(source_only) is not bool or (source_only and any(value is not None for value in (
            expected_source, expected_environment_sha256, parent_dispatch_id))):
        raise ValueError("source_readiness_cannot_bind_authoritative_candidate_preflight")
    if execution_observation is not None and (not source_only or type(execution_observation) is not dict
                                              or execution_observation):
        raise ValueError("source_readiness_execution_observation_requires_empty_source_only_context")
    if root.resolve() != ROOT.resolve():
        raise ValueError("candidate_preflight_installation_owner_mismatch")
    if parent_dispatch_id is not None and not re.fullmatch(r"dispatch-[0-9]+-[a-f0-9]{32}", parent_dispatch_id):
        raise ValueError("candidate_preflight_parent_binding_invalid")
    payload = _load_profile()
    canonical_inventory, _ = _canonical_classification(payload)
    plan = candidate_preflight_plan(payload, root=root)
    operation = "source-readiness" if source_only else "candidate-preflight"
    print(f"[{operation}] START source_validators={len(plan['source_validators'])} "
          f"test_nodes={0 if source_only else len(plan['selected_nodes'])} coverage={plan['coverage']}", flush=True)
    identity = build_execution_identity(root)
    if expected_source is not None and identity["source"] != expected_source:
        raise ValueError("candidate_preflight_source_changed_before_launch")
    if expected_environment_sha256 is not None and identity["native_environment_sha256"] != expected_environment_sha256:
        raise ValueError("candidate_preflight_environment_changed_before_launch")
    ident = source_queue.id if source_queue is not None else operation + "-" + uuid.uuid4().hex
    directory = source_queue.directory if source_queue is not None else root / "output/reports/candidate-preflights" / ident
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("candidate_preflight_report_escapes_installation")
    if source_queue is None:
        directory.mkdir(parents=True, exist_ok=False)
    observation: dict = {}
    if source_queue is not None:
        source_queue.bind(plan, identity)
    source_observations = list(source_queue.results) if source_queue is not None else []
    previously_completed = len(source_observations)
    fixture_observation = None
    sample_observation = None
    atlas_observation = None
    lifecycle_observation = {"status": "NOT_REQUESTED_SOURCE_ONLY" if source_only else "LIFECYCLE_METADATA_UNAVAILABLE"}
    code, error, interrupted, source_end = None, None, None, None
    attempt_started = time.monotonic()
    deadline = attempt_started + plan["policy"]["timeout_seconds"] - (source_queue.spent if source_queue is not None else 0)
    source_finished = test_started = test_finished = None
    try:
        # Check once on the completing source-readiness attempt or standalone pre-freeze
        # preparation. The dispatched proof owns its later, full manual-pack check.
        remaining_steps = len(plan["source_validators"]) - previously_completed
        if parent_dispatch_id is None and (max_source_steps is None or remaining_steps <= max_source_steps):
            from tools import validate_atlas_sqlite_first_access as atlas_access

            atlas_observation = atlas_access.preparation_observation(root)
            if atlas_observation["status"] != "PASS":
                raise ValueError("pre_freeze_atlas_access_failed")
            sample_observation = manual_sample_readiness_observation()
            if sample_observation["status"] != "MANUAL_SAMPLES_READY":
                raise ValueError("pre_freeze_manual_sample_review_required")
        if not source_only:
            lifecycle_observation = work_item_readiness_observation(root=root)
            if lifecycle_observation["status"] != "LIFECYCLE_METADATA_READY":
                raise ValueError("candidate_preflight_work_items_unresolved")
        fixture_observation = ({"status": "FIXTURE_INPUTS_NOT_REQUESTED_SOURCE_ONLY"} if source_only else
                               required_fixture_observation(payload, canonical_inventory, root=root, deadline=deadline))
        if fixture_observation['status'] == 'FIXTURE_INPUTS_FAIL':
            raise ValueError('candidate_preflight_fixture_inputs_failed')
        for step in plan["source_validators"][previously_completed:]:
            if max_source_steps is not None and len(source_observations) - previously_completed >= max_source_steps:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("candidate_preflight_execution_deadline")
            output_remaining = plan["policy"]["output_limit_bytes"] - sum(
                row["process"]["output_bytes"] for row in source_observations)
            if output_remaining <= 0:
                raise ValueError("candidate_preflight_output_budget")
            print(f"[{operation}] source_validator={step['id']}", flush=True)
            if source_queue is not None:
                source_queue.start_step(step)
            result = source_validator_observation(step, cwd=root, env=os.environ.copy(),
                limits={**plan["policy"], "timeout_seconds": remaining, "output_limit_bytes": output_remaining})
            if source_queue is not None:
                source_queue.finish_step(step, result)
            source_observations.append(result)
            if result["status"] != "SOURCE_VALIDATOR_PASS":
                break
        source_finished = time.monotonic()
        if (len(source_observations) == len(plan["source_validators"])
                and all(row["status"] == "SOURCE_VALIDATOR_PASS" for row in source_observations)
                and not source_only):
            remaining = deadline - source_finished
            if remaining <= 0:
                raise TimeoutError("candidate_preflight_execution_deadline")
            output_remaining = plan["policy"]["output_limit_bytes"] - sum(
                row["process"]["output_bytes"] for row in source_observations)
            if output_remaining <= 0:
                raise ValueError("candidate_preflight_output_budget")
            test_started = time.monotonic()
            code = _run_pytest(plan["selected_nodes"], target_fixture=None, collect_only=False,
                                     verbose=False, basetemp=None, identity=identity,
                                     observation=observation, diagnostic=True,
                                      preflight_limits={**plan["policy"], "timeout_seconds": plan["policy"]["test_timeout_seconds"],
                                                       "output_limit_bytes": output_remaining})
            test_finished = time.monotonic()
            if test_finished - test_started >= plan["policy"]["test_timeout_seconds"]:
                code = code or 124
    except BaseException as exc:
        error = type(exc).__name__
        if not isinstance(exc, Exception):
            interrupted = exc
    if source_finished is None:
        source_finished = time.monotonic()
    if test_started is not None and test_finished is None:
        test_finished = time.monotonic()
    try:
        source_end = source_state_identity(root)
    except Exception as exc:
        error = error or type(exc).__name__
    terminal = observation.get("terminal") or {}
    phases = terminal.get("phase_counts") or {}
    collection = observation.get("collection") or {}
    cases = observation.get("completed_preflight_cases") or []
    selected = set(plan["selected_nodes"])
    complete_functions = {case["test_path"] + "::" + case["case_name"] for case in cases}
    complete_case_ids = {case["nodeid_sha256"] for case in cases}
    source_passed = (len(source_observations) == len(plan["source_validators"])
                     and all(row["status"] == "SOURCE_VALIDATOR_PASS" for row in source_observations)
                     and error is None and source_end == identity["source"]
                     and time.monotonic() < deadline
                     and sum(row["process"]["output_bytes"] for row in source_observations)
                     <= plan["policy"]["output_limit_bytes"])
    passed = (lifecycle_observation["status"] == "LIFECYCLE_METADATA_READY"
              and fixture_observation is not None
              and fixture_observation['status'] in {'FIXTURE_INPUTS_VERIFIED', 'FIXTURE_INPUTS_NOT_APPLICABLE'}
              and len(source_observations) == len(plan["source_validators"])
              and all(row["status"] == "SOURCE_VALIDATOR_PASS" for row in source_observations)
              and code == 0 and error is None and observation.get("status") == "FINISHED"
              and terminal.get("failure_events") == 0 and phases.get("call:passed", 0) > 0
              and not any(key.endswith(":skipped") or key.endswith(":failed") for key in phases)
              and set(collection.get("selected_functions") or []) == selected == complete_functions
              and len(cases) == len(complete_case_ids) == collection.get("count")
              == phases.get("setup:passed") == phases.get("call:passed") == phases.get("teardown:passed")
              and source_end == identity["source"])
    paused = (source_queue is not None and error is None and source_end == identity["source"]
              and len(source_observations) < len(plan["source_validators"])
              and all(row["status"] == "SOURCE_VALIDATOR_PASS" for row in source_observations)
              and time.monotonic() < deadline)
    status = (("SOURCE_READINESS_PENDING" if paused else "SOURCE_READINESS_PASS" if source_passed else "SOURCE_READINESS_FAIL") if source_only else
              ("PREFLIGHT_PASS" if passed else "PREFLIGHT_FAIL"))
    receipt = {"schema": plan["schema"], "id": ident, "status": status,
               "mode": "source_readiness" if source_only else "candidate_preflight",
               "test_execution_requested": not source_only,
               "authority": AUTHORITY, "parent_dispatch_id": parent_dispatch_id, "plan": plan,
               "identity": identity, "source_end": source_end, "returncode": code,
               "error_type": error, "seconds": round(time.monotonic()-started, 3),
               "test_observation": observation, "actual_passed_cases": phases.get("call:passed", 0),
               "source_validator_observations": source_observations,
                "execution_phases": {
                    "source": {"budget_seconds": plan["policy"]["timeout_seconds"],
                               "previous_queue_seconds": source_queue.spent if source_queue is not None else 0,
                               "seconds": round(source_finished - attempt_started, 3)},
                    "tests": {"budget_seconds": 0 if source_only else plan["policy"]["test_timeout_seconds"],
                              "executed": test_started is not None,
                              "seconds": None if test_started is None else round(test_finished - test_started, 3)},
                    "maximum_execution_budget_seconds": plan["policy"]["timeout_seconds"] +
                        (0 if source_only else plan["policy"]["test_timeout_seconds"]),
                    "output_budget_shared": True},
                "fixture_input_observation": fixture_observation,
                "work_item_readiness_observation": lifecycle_observation,
               "manual_sample_readiness_observation": sample_observation,
               "atlas_access_readiness_observation": atlas_observation,
               "canonical_test_step_reuse": False, "publication_authority": False}
    if source_queue is not None:
        receipt["source_queue"] = {"queue_id": ident, "attempt": source_queue.attempt,
                                   "previously_completed": previously_completed,
                                   "executed_this_attempt": len(source_observations) - previously_completed,
                                   "selected_total": len(plan["source_validators"])}
    path = source_queue.receipt_path if source_queue is not None else directory / "receipt.json"
    with path.open("x", encoding="utf-8") as out:
        json.dump(receipt, out, ensure_ascii=False, indent=2)
        out.write("\n"); out.flush(); os.fsync(out.fileno())
    if source_queue is not None:
        queue_state = ("PAUSED" if paused else "COMPLETE" if source_passed else
                       "UNRESOLVED_EXECUTION" if source_queue.pending_step is not None else "VALIDATION_FAILED")
        source_queue.finish(queue_state, time.monotonic() - attempt_started)
    result = {"status": receipt["status"], "receipt": str(path),
              "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "actual_passed_cases": receipt["actual_passed_cases"], "authority": AUTHORITY,
              "canonical_test_step_reuse": False, "publication_authority": False}
    if source_queue is not None:
        result.update(queue_id=ident, queue_state=queue_state, source_queue=receipt["source_queue"])
    print(f"[{operation}] " + json.dumps(result), flush=True)
    if interrupted is not None:
        raise interrupted
    if source_only and source_passed and execution_observation is not None:
        execution_observation.update(identity=identity, profile_sha256=plan["profile_sha256"])
    return result

def main() -> int:
    parser = argparse.ArgumentParser(description="Run the authority-scoped Nexora SAGE test profile.")
    parser.add_argument(
        "--profile",
        default="auto",
        choices=["auto", "canonical_all", "public_target_repository"],
    )
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all-shards", action="store_true")
    mode.add_argument("--shard")
    mode.add_argument("--unit-pilot", help="Opt-in diagnostic unit experiment; never release-profile reuse")
    mode.add_argument("--incremental-unit", help="Reuse a clean persisted isolated development unit when its declared inputs match")
    mode.add_argument("--diagnostic-test", action="append", help="Owned test file/node; stops on first error, never a canonical receipt")
    mode.add_argument("--preflight-changed-file", action="append", help="Select reviewed early consumer checks from source changes; diagnostic only")
    mode.add_argument("--candidate-preflight", action="store_true", help="Execute reviewed preparation guards only; never freeze or run the aggregate")
    mode.add_argument("--source-readiness", action="store_true", help="Run central source guards only during development; no pytest, fixture preparation or candidate acceptance")
    mode.add_argument("--continue-source-readiness", metavar="QUEUE_ID", help="Continue only remaining source guards in an exact-context recorded queue; no retry of failed/unresolved work")
    parser.add_argument("--max-source-steps", type=int, help="Checkpoint a source-only attempt after at most this many new guards; does not reset its shared budgets")
    parser.add_argument("--receipt")
    parser.add_argument("--basetemp-root")
    args = parser.parse_args()

    try:
        if args.max_source_steps is not None and not (args.source_readiness or args.continue_source_readiness):
            raise ValueError("Source step limits require explicit source-only mode")
        if args.candidate_preflight or args.source_readiness or args.continue_source_readiness:
            if args.receipt or args.basetemp_root or args.profile != "auto" or args.collect_only:
                raise ValueError("Candidate preflight forbids profile/receipt/basetemp/collection overrides")
            if args.source_readiness or args.continue_source_readiness:
                options = {"source_only": True}
                if args.continue_source_readiness is not None: options["source_queue_id"] = args.continue_source_readiness
                if args.max_source_steps is not None: options["max_source_steps"] = args.max_source_steps
                result = run_candidate_preflight(**options)
                return 0 if result["status"] in {"SOURCE_READINESS_PASS", "SOURCE_READINESS_PENDING", "SOURCE_READINESS_RECORDED"} else 1
            result = run_candidate_preflight()
            return 0 if result["status"] == "PREFLIGHT_PASS" else 1
        if args.incremental_unit:
            if args.receipt or args.collect_only or args.basetemp_root or args.profile != "auto" or args.verbose:
                raise ValueError("Incremental units forbid profile/receipt/basetemp/collection/verbosity overrides")
            from tools.core.distribution_test_unit_reuse import run_unit

            result = run_unit(ROOT, args.incremental_unit)
            print("[distribution-unit] " + json.dumps(result), flush=True)
            return 0 if result["status"] == "PASS" else 1
        if args.unit_pilot:
            if not args.receipt or args.collect_only or args.basetemp_root or args.profile != "auto":
                raise ValueError("--unit-pilot requires --receipt and forbids profile/collect-only/basetemp options")
            from tools.core.distribution_test_unit_pilot import run_pilot

            receipt_path = Path(args.receipt)
            if not receipt_path.is_absolute():
                receipt_path = ROOT / receipt_path
            if (not receipt_path.resolve().is_relative_to((ROOT / "output/reports").resolve())
                    or receipt_path.suffix != ".json" or receipt_path.exists()):
                raise ValueError("Pilot requires a new diagnostic JSON report; managed/canonical or existing receipts are forbidden")
            receipt = run_pilot(ROOT, args.unit_pilot)
            if receipt_path.exists():
                raise ValueError("Pilot receipt appeared during execution; refusing overwrite")
            _write_json_atomic(receipt_path, receipt)
            print(f"[distribution-unit-pilot] {receipt['status']} canonical_profile_reuse_enabled=False", flush=True)
            return 0 if receipt["status"] == "DIAGNOSTIC_PASS" else 1
        if args.receipt and not (args.all_shards or args.shard):
            raise ValueError("--receipt requires --all-shards or --shard")
        if args.basetemp_root and not (args.all_shards or args.shard):
            raise ValueError("--basetemp-root requires --all-shards or --shard")
        profile_payload = _load_profile()
        partitions: dict[str, list[str]] | None = None
        selected_profile = ""
        unit_plans = {}
        diagnostic = bool(args.diagnostic_test or args.preflight_changed_file)
        if diagnostic:
            if args.receipt or args.basetemp_root or args.profile != "auto" or "public_projection" in profile_payload:
                raise ValueError("Diagnostics require canonical source and forbid profile/canonical receipt/basetemp overrides")
            from tools import validate_atlas_sqlite_first_access as atlas_access
            from tools.core import distribution_test_execution as execution

            atlas_paths = {path for path in args.preflight_changed_file or [] if atlas_access.affected_by_change(path)}
            if args.preflight_changed_file and any(
                    path not in atlas_paths and not execution.early_consumer_tests([path], profile_payload)
                    for path in args.preflight_changed_file):
                raise ValueError("Unmapped changed file in preflight request; no global-suite fallback")
            tests = args.diagnostic_test or execution.early_consumer_tests(args.preflight_changed_file, profile_payload)
            if atlas_paths and args.collect_only:
                print("[distribution-tests] selected source guard: atlas_sqlite_first_access")
            if atlas_paths and not tests:
                if args.collect_only:
                    return 0
                observed = atlas_access.preparation_observation(ROOT)
                print("[distribution-tests] atlas_sqlite_first_access " + json.dumps(observed))
                return 0 if observed["status"] == "PASS" else 1
            inventory = _canonical_classification(profile_payload)[0]
            if not tests or len(tests) != len(set(tests)) or any(test.split("::", 1)[0] not in inventory for test in tests):
                raise ValueError("Diagnostic selection must name unique classified tests")
            target_fixture = None
        elif args.all_shards or args.shard:
            partitions, target_fixture, selected_profile = _resolve_test_partitions_from_payload(
                profile_payload, args.profile
            )
            if args.shard and args.shard not in partitions:
                raise ValueError(f"Unknown distribution test shard: {args.shard}")
            tests = [path for shard_tests in partitions.values() for path in shard_tests]
            if (not args.collect_only and selected_profile == "canonical_all"
                    and "public_projection" not in profile_payload
                    and profile_payload.get("profiles", {}).get(selected_profile, {}).get("execution_units_contract") is not None):
                from tools.core.distribution_test_family_units import plan_families

                unit_plans = plan_families(ROOT, profile_payload, selected_profile,
                    {args.shard: partitions[args.shard]} if args.shard else partitions)
        else:
            tests, target_fixture = _resolve_tests_from_payload(profile_payload, args.profile)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"[distribution-tests] FAIL: {exc}", file=sys.stderr)
        return 2

    try:
        identity = None
        if args.preflight_changed_file and not args.collect_only:
            source_context: dict = {}
            preparation = run_candidate_preflight(source_only=True, execution_observation=source_context)
            if preparation["status"] != "SOURCE_READINESS_PASS":
                return 1
            identity = source_context.get("identity")
            if (not isinstance(identity, dict) or not identity.get("source")
                    or source_context.get("profile_sha256") != execution.digest(profile_payload)):
                raise ValueError("source_readiness_changed_selection_or_missing_launch_context")
            print("[distribution-tests] phase=source_ready_using_same_launch_identity", flush=True)
        elif "public_projection" not in profile_payload:
            from tools.core.distribution_test_execution import build_execution_identity

            # One capture shared by the selected families, not one expensive
            # environment scan per family. The test-only overlay is bound separately.
            print("[distribution-tests] phase=capturing_launch_identity", flush=True)
            identity = build_execution_identity(ROOT)
        print(
            f"[distribution-tests] profile={args.profile} tests={len(tests)} "
            f"public_distribution={PUBLIC_MANIFEST.is_file()} collect_only={args.collect_only} "
            f"partitioned={bool(partitions)}",
            flush=True,
        )
        if partitions is None:
            return _run_pytest(
                tests,
                target_fixture=target_fixture,
                collect_only=args.collect_only,
                verbose=args.verbose,
                basetemp=None,
                identity=identity,
                diagnostic=diagnostic,
            )

        selected_families = [args.shard] if args.shard else list(partitions)
        shard_results: list[dict[str, Any]] = []
        basetemp_root = Path(args.basetemp_root) if args.basetemp_root else None
        capture_scope = nullcontext(None)
        if sum(len(plan["units"]) for plan in unit_plans.values()) > 1:
            from tools.core.distribution_test_unit_reuse import SharedUnitInputs

            capture_scope = SharedUnitInputs(ROOT)
        with capture_scope as shared_inputs:
            for family in selected_families:
                shard_tests = partitions[family]
                print(
                    f"[distribution-tests] shard={family} tests={len(shard_tests)}",
                    flush=True,
                )
                observation: dict = {}
                composition = None
                def run_remaining(paths, family_parent_context_sha256=None):
                    diagnostics: dict = {}
                    code = _run_pytest(paths,
                        target_fixture=target_fixture,
                        collect_only=args.collect_only,
                        verbose=args.verbose,
                        basetemp=(basetemp_root / family if basetemp_root else None),
                        identity=identity,
                        observation=diagnostics,
                        **({"family_parent_context_sha256": family_parent_context_sha256}
                           if family_parent_context_sha256 is not None else {}))
                    return code, diagnostics
                if family in unit_plans:
                    from tools.core.distribution_test_family_units import run_family

                    returncode, composition = run_family(ROOT, payload=profile_payload, family=family,
                        tests=shard_tests, identity=identity, plan=unit_plans[family], run_remainder=run_remaining,
                        **({"shared_inputs": shared_inputs} if shared_inputs is not None else {}))
                    observation = composition["remaining"].get("diagnostics", {})
                else:
                    returncode, observation = run_remaining(shard_tests)
                shard_results.append(
                    {
                        "id": family,
                        "status": "PASS" if returncode == 0 else "FAIL",
                        "returncode": returncode,
                        "test_count": len(shard_tests),
                        "test_paths_sha256": _canonical_sha256(shard_tests),
                        "test_paths": shard_tests,
                        "diagnostics": observation,
                        **({"unit_composition": composition} if composition is not None else {}),
                    }
                )

        status = "PASS" if all(row["returncode"] == 0 for row in shard_results) else "FAIL"
        receipt = {
            "schema": "nexora_sage_distribution_test_partition_receipt_v2" if unit_plans else "nexora_sage_distribution_test_partition_receipt_v1",
            "status": status,
            "authority": "test_evidence_only_no_release_or_publication_authority",
            "profile": selected_profile,
            "collect_only": bool(args.collect_only),
            "partition_contract_sha256": _canonical_sha256(profile_payload["partition"]),
            "selected_test_union_sha256": _canonical_sha256(
                [path for family in selected_families for path in partitions[family]]
            ),
            "selected_test_count": sum(len(partitions[family]) for family in selected_families),
            "exact_profile_union": not bool(args.shard),
            "shards": shard_results,
        }
        if args.receipt:
            receipt_path = Path(args.receipt)
            if not receipt_path.is_absolute():
                receipt_path = ROOT / receipt_path
            _write_json_atomic(receipt_path, receipt)
        print(
            f"[distribution-tests] shard_union={status} shards={len(shard_results)} "
            f"tests={receipt['selected_test_count']}",
            flush=True,
        )
        return 0 if status == "PASS" else 1
    except (OSError, ValueError) as exc:
        print(f"[distribution-tests] FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
