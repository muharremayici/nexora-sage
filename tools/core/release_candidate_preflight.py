"""Early consumer preparation, separate from canonical proof/reuse authority."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import math
import uuid

from tools.core.distribution_test_execution import digest, execution_contract
from tools.core.source_maintainability import require_managed_large_sources
from tools.core.subprocess_telemetry import run_observed_subprocess


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = "preparation_only_no_test_step_reuse_or_publication"


class SourceQueueRefusal(ValueError):
    """A recorded queue cannot safely start another source command."""


class SourceReadinessQueue:
    """Single-writer, append-only local preparation; never canonical proof reuse."""

    def __init__(self, root: Path, queue_id: str | None = None, *, review: bool = False):
        self.root = root.resolve()
        if type(review) is not bool:
            raise SourceQueueRefusal("INVALID_QUEUE_MODE")
        self.review = review
        prefix = "review-readiness-" if review else "source-readiness-"
        self.resuming = queue_id is not None
        self.id = queue_id if queue_id is not None else prefix + uuid.uuid4().hex
        if not isinstance(self.id, str) or not re.fullmatch(re.escape(prefix) + r"[a-f0-9]{32}", self.id):
            raise SourceQueueRefusal("INVALID_QUEUE_ID")
        self.base = self.root / "output/reports/candidate-preflights"
        self.directory = self.base / self.id
        self.events: list[dict] = []
        self.results: list[dict] = []
        self.spent = 0.0
        self.attempt = 0
        self.pending_step = None

    def __enter__(self):
        from tools.core.advisory_file_lock import AdvisoryFileLock

        if self.base.resolve() != self.base or not self.base.resolve().is_relative_to(self.root):
            raise SourceQueueRefusal("UNSAFE_QUEUE_STORAGE")
        lock_path = self.base / ".source-readiness.lock"
        if lock_path.is_symlink() or not lock_path.resolve().is_relative_to(self.root):
            raise SourceQueueRefusal("UNSAFE_QUEUE_LOCK")
        self.lock = AdvisoryFileLock(lock_path)
        if not self.lock.acquire():
            raise SourceQueueRefusal("QUEUE_BUSY")
        try:
            if self.directory.is_symlink() or self.directory.resolve() != self.directory:
                raise SourceQueueRefusal("UNSAFE_QUEUE_PATH")
            if self.resuming:
                if not self.directory.is_dir():
                    raise SourceQueueRefusal("QUEUE_NOT_FOUND")
            else:
                self.directory.mkdir(exist_ok=False)
            return self
        except BaseException:
            self.lock.release()
            raise

    def __exit__(self, *exc):
        self.lock.release()

    def _file(self, name: str) -> Path:
        if Path(name).name != name or name in {".", ".."}:
            raise SourceQueueRefusal("UNSAFE_QUEUE_FILE")
        path = self.directory / name
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()):
            raise SourceQueueRefusal("UNSAFE_QUEUE_FILE")
        return path

    def _append(self, event: str, **values) -> None:
        row = {"seq": len(self.events), "previous_sha256": self.events[-1]["sha256"] if self.events else None,
               "event": event, **values}
        row["sha256"] = digest(row)
        encoded = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
        path = self._file("source-queue.jsonl")
        size = path.stat().st_size if path.exists() else 0
        if size + len(encoded) > self.limit:
            raise SourceQueueRefusal("QUEUE_JOURNAL_BUDGET")
        with path.open("ab") as out:
            out.write(encoded); out.flush(); os.fsync(out.fileno())
        self.events.append(row)

    def _verify_file(self, reference: dict) -> None:
        path = self._file(reference["name"])
        with path.open("rb") as stream:
            content = stream.read(self.limit + 1)
        if len(content) > self.limit or hashlib.sha256(content).hexdigest() != reference["sha256"]:
            raise SourceQueueRefusal("QUEUE_EVIDENCE_CHANGED")

    def _snapshot(self, step: dict, role: str) -> dict | None:
        from tools.core.json_io import load_raw_artifact_primary_with_identity

        primary = load_raw_artifact_primary_with_identity(Path(step["raw_artifact"]))
        if primary is None:
            return None
        payload, identity = primary
        name = f"{self.attempt}-{len(self.results)}-{role}.json"
        content = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if len(content) > self.limit:
            raise SourceQueueRefusal("QUEUE_SNAPSHOT_BUDGET")
        with self._file(name).open("xb") as out:
            out.write(content); out.flush(); os.fsync(out.fileno())
        return {"name": name, "sha256": hashlib.sha256(content).hexdigest(), "primary_identity": identity}

    def bind(self, plan: dict, identity: dict) -> None:
        from tools.core.json_syntax import loads_json_strict
        from tools.core.json_io import load_raw_artifact_primary_with_identity

        if (plan.get("mode") == "review_readiness") != self.review:
            raise SourceQueueRefusal("QUEUE_PLAN_MODE_MISMATCH")
        self.limit = plan["policy"]["output_limit_bytes"]
        self.steps = plan["source_validators"]
        frame = {"identity": identity, "plan_sha256": digest(plan)}
        if not self.resuming:
            self._append("queue_created", schema=plan["policy"]["source_queue"]["schema"], frame=frame)
        else:
            try:
                with self._file("source-queue.jsonl").open("rb") as stream:
                    content = stream.read(self.limit + 1)
                if len(content) > self.limit or not content.endswith(b"\n"):
                    raise SourceQueueRefusal("QUEUE_JOURNAL_INVALID")
                for line in content.splitlines():
                    row = loads_json_strict(line.decode("utf-8"))
                    if not isinstance(row, dict):
                        raise SourceQueueRefusal("QUEUE_JOURNAL_INVALID")
                    body = {k: v for k, v in row.items() if k != "sha256"}
                    if (row["seq"] != len(self.events) or row["sha256"] != digest(body)
                            or row["previous_sha256"] != (self.events[-1]["sha256"] if self.events else None)):
                        raise SourceQueueRefusal("QUEUE_JOURNAL_INVALID")
                    self.events.append(row)
                if not self.events or self.events[0].get("event") != "queue_created":
                    raise SourceQueueRefusal("QUEUE_JOURNAL_INVALID")
                if self.events[0].get("schema") != plan["policy"]["source_queue"]["schema"]:
                    raise SourceQueueRefusal("QUEUE_JOURNAL_INVALID")
                if self.events[0].get("frame") != frame:
                    raise SourceQueueRefusal("STALE_CONTEXT")
                if identity.get("native_environment_complete") is not True:
                    raise SourceQueueRefusal("INCOMPLETE_ENVIRONMENT")
                active = False
                last_state = None
                for row in self.events[1:]:
                    kind = row["event"]
                    if kind == "attempt_started" and not active:
                        if row["attempt"] != self.attempt + 1:
                            raise SourceQueueRefusal("QUEUE_JOURNAL_INVALID")
                        active = True; self.attempt += 1
                    elif kind == "step_started" and active and self.pending_step is None:
                        if row["id"] != self.steps[len(self.results)]["id"]:
                            raise SourceQueueRefusal("QUEUE_JOURNAL_INVALID")
                        if row.get("prior"): self._verify_file(row["prior"])
                        self.pending_step = row["id"]
                    elif kind == "step_finished" and active and row["result"]["id"] == self.pending_step:
                        if row.get("snapshot"): self._verify_file(row["snapshot"])
                        self.results.append(row["result"]); self.pending_step = None
                    elif kind == "attempt_finished" and active:
                        seconds = row["budget_spent_seconds"]
                        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
                            raise SourceQueueRefusal("QUEUE_JOURNAL_INVALID")
                        self._verify_file(row["receipt"])
                        self.spent += seconds; active = False; last_state = row["state"]
                    else:
                        raise SourceQueueRefusal("QUEUE_JOURNAL_INVALID")
                if active or self.pending_step is not None:
                    raise SourceQueueRefusal("UNRESOLVED_ATTEMPT")
                if self.review and last_state == "WAITING":
                    raise SourceQueueRefusal("WAITING_NO_AUTOMATIC_REEXECUTION")
                if last_state not in {"PAUSED", "COMPLETE"}:
                    raise SourceQueueRefusal("FAILED_QUEUE_NO_RETRY")
                if (len(self.results) == len(self.steps)) != (last_state == "COMPLETE"):
                    raise SourceQueueRefusal("QUEUE_JOURNAL_INVALID")
                for step, result in zip(self.steps, self.results):
                    primary = load_raw_artifact_primary_with_identity(Path(step["raw_artifact"]))
                    current_status = None
                    if primary is not None and isinstance(primary[0], dict):
                        summary = primary[0].get("summary")
                        current_status = primary[0].get("status", summary.get("status") if isinstance(summary, dict) else None)
                    if (result.get("status") != ("REVIEW_STEP_PASS" if self.review else "SOURCE_VALIDATOR_PASS") or result.get("process", {}).get("returncode") != 0
                            or primary is None or not isinstance(primary[0], dict)
                            or primary[1] != result.get("artifact_primary_identity")
                            or current_status != "PASS"):
                        raise SourceQueueRefusal("QUEUE_EVIDENCE_CHANGED")
                if last_state == "COMPLETE":
                    raise SourceQueueRefusal("ALREADY_RECORDED")
            except (OSError, KeyError, IndexError, TypeError, UnicodeError, ValueError) as exc:
                if isinstance(exc, SourceQueueRefusal): raise
                raise SourceQueueRefusal("QUEUE_JOURNAL_INVALID") from exc
        if self.spent >= plan["policy"]["timeout_seconds"]:
            raise SourceQueueRefusal("QUEUE_TIME_BUDGET")
        self.attempt += 1
        self.receipt_path = self._file("receipt.json" if self.attempt == 1 else f"receipt-{self.attempt}.json")
        self._append("attempt_started", attempt=self.attempt)

    def start_step(self, step: dict) -> None:
        prior = self._snapshot(step, "prior")
        self._append("step_started", id=step["id"], prior=prior)
        self.pending_step = step["id"]

    def finish_step(self, step: dict, result: dict) -> None:
        snapshot = self._snapshot(step, "result")
        if result.get("status") in {"SOURCE_VALIDATOR_PASS", "REVIEW_STEP_PASS"} and (
                result.get("process", {}).get("returncode") != 0 or snapshot is None
                or snapshot["primary_identity"] != result.get("artifact_primary_identity")):
            raise SourceQueueRefusal("QUEUE_RESULT_CONTRADICTION")
        if self.review and result.get("status") == "REVIEW_STEP_WAITING" and (
                snapshot is None or snapshot["primary_identity"] != result.get("artifact_primary_identity")):
            raise SourceQueueRefusal("QUEUE_RESULT_CONTRADICTION")
        self._append("step_finished", result=result, snapshot=snapshot)
        self.results.append(result); self.pending_step = None

    def finish(self, state: str, seconds: float) -> None:
        self._append("attempt_finished", state=state, budget_spent_seconds=seconds,
                     receipt={"name": self.receipt_path.name,
                              "sha256": hashlib.sha256(self.receipt_path.read_bytes()).hexdigest()})


def _relative(value: object) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", value)
            or PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts
            or PurePosixPath(value).as_posix() != value):
        raise ValueError("invalid_preflight_source_path")
    return value


def candidate_preflight_plan(payload: dict, *, root: Path = ROOT) -> dict:
    if "public_projection" in payload:
        raise ValueError("candidate_preflight_requires_maintainer_source")
    contract = execution_contract(payload)
    require_managed_large_sources(root)
    policy = contract.get("candidate_preflight")
    if (not isinstance(policy, dict)
            or set(policy) != {"schema", "protocol", "applies_to", "authority", "always_execute", "timeout_seconds", "test_timeout_seconds", "output_limit_bytes", "maximum_test_nodes", "maximum_cases", "coverage", "source_validator_steps", "source_queue", "work_item_readiness"}
            or policy.get("schema") != "nexora_candidate_preflight_v1"
            or policy.get("protocol") != "owned_empty_pytest_config_no_filters_or_autoload_v1"
            or policy.get("applies_to") != "authoritative_frozen_candidate_full_aggregate"
            or policy.get("authority") != AUTHORITY or policy.get("always_execute") is not True
            or policy.get("coverage") != "reviewed_early_consumers_only"
            or type(policy.get("timeout_seconds")) is not int or not 1 <= policy["timeout_seconds"] <= 600
            or type(policy.get("test_timeout_seconds")) is not int or not 1 <= policy["test_timeout_seconds"] <= 600
            or type(policy.get("output_limit_bytes")) is not int or not 1024 <= policy["output_limit_bytes"] <= 16777216
            or type(policy.get("maximum_test_nodes")) is not int or not 1 <= policy["maximum_test_nodes"] <= 32
            or type(policy.get("maximum_cases")) is not int or not 1 <= policy["maximum_cases"] <= 128):
        raise ValueError("invalid_candidate_preflight_policy")
    if policy["source_queue"] != {"schema": "nexora_source_readiness_queue_v1",
                                  "resume": "exact_complete_context_successful_prefix",
                                  "failed_or_unresolved_attempt": "preserve_and_block_no_retry"}:
        raise ValueError("invalid_source_readiness_queue_policy")
    if policy["work_item_readiness"] != {
            "owner": "tools/core/work_item_readiness.py",
            "registry": "config/sage_work_item_registry.json",
            "phase": "pre_freeze_completion_metadata",
            "runtime_evidence": "required_later_by_canonical_proof"}:
        raise ValueError("invalid_candidate_work_item_readiness_policy")
    dependency_path = root / "config/release_validation_dependency_contract.json"
    dependency_bytes = dependency_path.read_bytes()
    dependency = json.loads(dependency_bytes)
    if (not isinstance(dependency, dict) or not isinstance(dependency.get("authorities"), dict)
            or dependency["authorities"].get("test_inventory") != "config/distribution_test_profiles.json"):
        raise ValueError("candidate_preflight_test_inventory_owner_mismatch")
    if dependency["authorities"].get("proof_steps") != "config/release_proof_steps_contract.json":
        raise ValueError("candidate_preflight_proof_steps_owner_mismatch")
    step_bytes = (root / dependency["authorities"]["proof_steps"]).read_bytes()
    step_contract = json.loads(step_bytes)
    definitions = step_contract.get("steps") if isinstance(step_contract, dict) else None
    ids = policy.get("source_validator_steps")
    if (not isinstance(definitions, list) or not definitions
            or not isinstance(ids, list) or not ids
            or any(not isinstance(value, str) for value in ids) or len(ids) != len(set(ids))):
        raise ValueError("invalid_candidate_preflight_source_validators")
    if any(not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] for row in definitions):
        raise ValueError("invalid_candidate_preflight_source_validator_definition")
    by_id = {row["id"]: row for row in definitions}
    if len(by_id) != len(definitions):
        raise ValueError("ambiguous_candidate_preflight_source_validator")
    source_validators, preceding = [], set()
    for ident in ids:
        step = by_id.get(ident)
        if (not isinstance(step, dict) or step.get("required") is not True
                or not isinstance(step.get("depends_on", []), list)
                or any(not isinstance(value, str) for value in step.get("depends_on", []))
                or not set(step.get("depends_on", [])) <= preceding):
            raise ValueError("candidate_preflight_source_dependency_missing_or_unordered")
        command, artifact = step.get("command"), step.get("raw_artifact")
        if (not isinstance(command, list) or len(command) != 2 or command[0] != "${python}"
                or not isinstance(command[1], str)
                or not re.fullmatch(r"\$\{code_maps\}/tools/validate_[a-z0-9_]+\.py", command[1])
                or not isinstance(artifact, str)
                or not re.fullmatch(r"\$\{raw\}/[a-z0-9_]+_validation\.json", artifact)):
            raise ValueError("candidate_preflight_requires_owned_source_validator")
        source = root / command[1].removeprefix("${code_maps}/")
        if not source.is_file() or not source.resolve().is_relative_to(root.resolve()):
            raise ValueError("candidate_preflight_source_validator_unavailable")
        artifact_path = root / "output/.raw" / artifact.removeprefix("${raw}/")
        if not artifact_path.resolve().is_relative_to(root.resolve()):
            raise ValueError("candidate_preflight_source_artifact_outside_installation")
        source_validators.append({"id": ident, "command": [sys.executable, str(source)],
            "raw_artifact": str(artifact_path),
            "step_contract_sha256": digest(step)})
        preceding.add(ident)
    nodes, ids, parsed = set(), set(), {}
    for row in contract["early_checks"]:
        ident = row.get("id")
        selected = row.get("freeze_test_nodes")
        if (not isinstance(ident, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", ident)
                or ident in ids or not isinstance(selected, list) or not selected
                or not all(isinstance(node, str) for node in selected)
                or len(selected) != len(set(selected))):
            raise ValueError("invalid_candidate_preflight_mapping")
        ids.add(ident)
        for path in row["changed_paths"]:
            _relative(path)
        for node in selected:
            if not isinstance(node, str) or node.count("::") != 1:
                raise ValueError("candidate_preflight_requires_exact_test_functions")
            path, name = node.split("::")
            _relative(path)
            if path not in row["test_paths"] or not re.fullmatch(r"test_[A-Za-z0-9_]{1,160}", name):
                raise ValueError("candidate_preflight_node_outside_reviewed_consumer")
            source = root / path
            if not source.resolve().is_relative_to(root.resolve()) or not source.is_file():
                raise ValueError("candidate_preflight_consumer_unavailable")
            if path not in parsed:
                try:
                    parsed[path] = {n.name for n in ast.parse(source.read_text("utf-8-sig")).body
                                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                except (OSError, UnicodeError, SyntaxError):
                    raise ValueError("candidate_preflight_consumer_parse_unavailable") from None
            if name not in parsed[path]:
                raise ValueError("candidate_preflight_consumer_function_missing")
            nodes.add(node)
    if not nodes or len(nodes) > policy["maximum_test_nodes"]:
        raise ValueError("candidate_preflight_selection_budget")
    return {"schema": policy["schema"], "authority": AUTHORITY, "policy": policy,
            "selected_nodes": sorted(nodes), "mapping_ids": sorted(ids),
            "coverage": policy["coverage"], "profile_sha256": digest(payload),
            "dependency_contract_sha256": hashlib.sha256(dependency_bytes).hexdigest(),
            "proof_steps_contract_sha256": hashlib.sha256(step_bytes).hexdigest(),
            "source_validators": source_validators,
            "canonical_test_step_reuse": False, "publication_authority": False}


def work_item_readiness_observation(*, root: Path) -> dict:
    """Read lifecycle SSoT; do not run a validator or consume its prior verdict."""
    from tools.core.json_io import load_json_object_strict
    from tools.core.work_item_readiness import assess_work_item_preparation

    registry_path = root / "config/sage_work_item_registry.json"
    if not registry_path.resolve().is_relative_to(root.resolve()):
        raise ValueError("candidate_work_item_registry_outside_installation")
    registry = load_json_object_strict(registry_path)
    return {**assess_work_item_preparation(registry, root=root),
            "registry": "config/sage_work_item_registry.json",
            "registry_semantic_sha256": digest(registry)}


def source_validator_observation(step: dict, *, cwd: Path, env: dict[str, str], limits: dict) -> dict:
    """Delegate the registered validator, then read its SQLite primary verdict."""
    from tools.core.json_io import load_raw_artifact_primary_with_identity

    process = supervise_preflight(step["command"], cwd=cwd, env=env, limits=limits)
    result = {"id": step["id"], "process": process, "status": "SOURCE_VALIDATOR_FAIL",
              "artifact_primary_identity": None, "artifact_status": None}
    if process["returncode"] != 0:
        return result
    try:
        primary = load_raw_artifact_primary_with_identity(Path(step["raw_artifact"]))
        if primary is not None:
            payload, identity = primary
            status = payload.get("status", payload.get("summary", {}).get("status")) if isinstance(payload, dict) else None
            result.update(artifact_primary_identity=identity, artifact_status=status)
            if status == "PASS" and isinstance(identity, str) and re.fullmatch(r"sqlite:[a-f0-9]{64}", identity):
                result["status"] = "SOURCE_VALIDATOR_PASS"
    except Exception as exc:
        return {**result, "status": "SOURCE_VALIDATOR_FAIL", "artifact_error_type": type(exc).__name__}
    return result


def required_fixture_observation(payload: dict, test_paths, *, root: Path, deadline: float) -> dict:
    """Acquire current required fixture inputs; never install or borrow old receipts."""
    from tools.core.react_typings_fixture import (
        ReactFixtureInputError, fixture_declaration, read_react_typings_fixture,
    )

    try:
        spec = fixture_declaration(payload)
        private = payload.get('classification', {}).get('overrides', {}).get('private_maintainer', [])
        if spec['consumer_test_path'] not in private:
            raise ReactFixtureInputError('fixture_consumer_not_private')
        if spec['consumer_test_path'] not in test_paths:
            return {'status': 'FIXTURE_INPUTS_NOT_APPLICABLE',
                    'canonical_test_step_reuse': False, 'publication_authority': False}
        return read_react_typings_fixture(root, payload=payload, materialize=False,
                                         deadline=deadline)['observation']
    except ReactFixtureInputError as exc:
        return {'status': 'FIXTURE_INPUTS_FAIL', 'reason': str(exc), 'inputs': getattr(exc, 'inputs', []),
                'canonical_test_step_reuse': False, 'publication_authority': False}


def supervise_preflight(command: list[str], *, cwd: Path, env: dict[str, str], limits: dict) -> dict:
    """Shared bounded process control only; the separate journal owns test evidence.

    Raw-observer diagnostics retain their no-test-admission authority. No stdout
    payload or stream digest is interpreted as a pytest result or reuse receipt.
    """
    observed = run_observed_subprocess(command, cwd=cwd, env=env,
        label="candidate-preflight", timeout=limits["timeout_seconds"],
        raw_observation=True, log=lambda message: print("[candidate-preflight] " + message, flush=True))
    size = observed.stdout.bytes_seen + observed.stderr.bytes_seen
    overflow = size > limits["output_limit_bytes"]
    code = (124 if observed.timed_out else 125 if overflow or not observed.capture_complete
            else observed.returncode)
    return {"returncode": code, "timed_out": observed.timed_out,
            "output_limit_exceeded": overflow, "output_bytes": size,
            "seconds": observed.duration_seconds, "raw_output_published": False,
            "process_observer_authority": observed.authority,
            "process_observer_policy_sha256": observed.policy_sha256,
            "process_observer_module_sha256": observed.observer_module_sha256,
            "process_capture_complete": observed.capture_complete,
            "stdout_sha256": observed.stdout.sha256, "stderr_sha256": observed.stderr.sha256,
            "canonical_test_step_reuse": False, "publication_authority": False}
