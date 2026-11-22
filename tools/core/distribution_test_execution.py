"""Owned pytest launch context and bounded failure observations, never proof reuse.

The profile owns synthetic prerequisites and early consumer selection. This module
is also an explicitly loaded pytest plugin; it records no traceback or environment
values. Its journal survives pytest basetemp cleanup and an interrupted child.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import uuid
from collections import Counter
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
CONTEXT_ENV = "SAGE_DISTRIBUTION_TEST_CONTEXT"
JOURNAL_ENV = "SAGE_DISTRIBUTION_TEST_JOURNAL"
PLUGIN = "tools.core.distribution_test_execution"
PREFLIGHT_CONFIG_NAME = "pytest.ini"
from tools.core.distribution_test_selection import (
    _STARTUP_PROTOCOLS, _STARTUP_COMPONENT_HASHES,
    validate_owned_probe_failure_policy,
)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def execution_contract(payload: dict | None = None) -> dict:
    if payload is None:
        payload = json.loads((ROOT / "config/distribution_test_profiles.json").read_text("utf-8"))
    from tools.core.distribution_test_selection import validate_execution_contract
    return validate_execution_contract(payload)


def synthetic_signing_key() -> str:
    """Public fixture constant, not a configured key or an authorization source."""
    return execution_contract()["synthetic_signing"]["public_test_value"]


def require_synthetic_signing_environment() -> None:
    signing = execution_contract()["synthetic_signing"]
    # No assert on os.environ: pytest assertion rewriting can expand the entire
    # environment. Refusal must not format either expected or inherited values.
    if any(os.environ.get(key) != signing["public_test_value"]
           for key in signing["environment_keys"]):
        raise RuntimeError("synthetic_signing_test_launch_required") from None


def child_environment(inherited: dict[str, str], tests: list[str], contract: dict) -> tuple[dict, dict]:
    env = dict(inherited)
    env.pop(CONTEXT_ENV, None)
    env.pop(JOURNAL_ENV, None)
    selected = {test.split("::", 1)[0] for test in tests}
    signing = contract["synthetic_signing"]
    applied = bool(selected.intersection(signing["test_paths"]))
    if applied:
        for key in signing["environment_keys"]:
            env[key] = signing["public_test_value"]
    return env, {"synthetic_signing_applied": applied, "contract_sha256": digest(contract)}


def early_consumer_tests(changed: list[str], payload: dict) -> list[str]:
    contract = execution_contract(payload)
    affected = set(changed)
    selected = set()
    for row in contract["early_checks"]:
        changed_tests = affected.intersection(row["test_paths"])
        if changed_tests or affected.intersection(row["changed_paths"]):
            # Editing a test file retains its full reviewed consumer set. Only
            # declared source changes may use an explicitly reviewed short slice.
            selected.update(row["test_paths"] if changed_tests else
                            row.get("development_test_nodes", row["test_paths"]))
    full_files = {test for test in selected if "::" not in test}
    return sorted(test for test in selected if "::" not in test or test.split("::", 1)[0] not in full_files)


def build_execution_identity(root: Path) -> dict:
    from tools.core.source_state_identity import source_state_identity
    from tools.core.release_proof_resume import environment_receipt

    environment = environment_receipt()
    return {"source": source_state_identity(root),
            "native_environment_sha256": environment["receipt_sha256"],
            "native_environment_complete": environment["complete"]}


def prepare_launch(root: Path, tests: list[str], inherited: dict[str, str],
                   contract: dict, *, identity: dict, collect_only: bool,
                    preflight: dict | None = None,
                    family_parent_context_sha256: str | None = None) -> tuple[dict, Path, dict]:
    env, overlay = child_environment(inherited, tests, contract)
    # Only source-owned test paths enter the journal; parameter text never does.
    paths = sorted({test.split("::", 1)[0] for test in tests})
    if not paths or any(not re.fullmatch(r"tools/tests/test_[a-z0-9_]+\.py", p) for p in paths):
        raise ValueError("owned_test_paths_required")
    context = {"invocation_id": "pytest-" + uuid.uuid4().hex,
               "identity": identity, "selection_sha256": digest(tests),
               "test_paths": paths, "collect_only": collect_only,
               "overlay": overlay, "diagnostics": contract["diagnostics"],
               "authority": "diagnostic_only_no_cache_release_or_publication_authority"}
    if family_parent_context_sha256 is not None:
        if (preflight is not None or not isinstance(family_parent_context_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", family_parent_context_sha256)):
            raise ValueError("invalid_family_journal_context")
        context["family_parent_context_sha256"] = family_parent_context_sha256
    if contract['diagnostics'].get('startup_observation') is not None:
        context['launch_root_sha256'] = digest(str(root.resolve()))
    if preflight is not None:
        env.pop("PYTEST_ADDOPTS", None)
        env.pop("PYTEST_PLUGINS", None)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        overlay["preflight_filtering_disabled"] = True
        context["candidate_preflight"] = {"protocol": preflight["protocol"],
            "maximum_cases": preflight["maximum_cases"], "selected_nodes": list(tests),
            "config_sha256": hashlib.sha256(b"[pytest]\n").hexdigest()}
    directory = root / "output/reports/distribution-test-runs" / context["invocation_id"]
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("test_journal_path_escapes_installation")
    directory.mkdir(parents=True, exist_ok=False)
    if preflight is not None:
        with (directory / PREFLIGHT_CONFIG_NAME).open("xb") as out:
            out.write(b"[pytest]\n")
    journal = directory / "events.jsonl"
    header = json.dumps({"event": "launch", "context": context,
                         "context_sha256": digest(context)}, ensure_ascii=False) + "\n"
    if len(header.encode("utf-8")) > contract["diagnostics"]["maximum_line_bytes"]:
        raise ValueError("test_launch_context_limit_exceeded")
    with journal.open("x", encoding="utf-8") as out:
        out.write(header)
        out.flush()
        os.fsync(out.fileno())
    # A digest, not the full test inventory, travels in the environment. This
    # also avoids Windows' per-variable size limit for canonical_all.
    env[CONTEXT_ENV] = digest(context)
    env[JOURNAL_ENV] = str(journal.resolve())
    return env, journal, context


def _startup_digest(value, budget: int) -> str | None:
    """Hash bounded JSON metadata only; never stringify arbitrary plugin objects."""
    pending = [(value, 0)]
    remaining = budget
    while pending:
        item, depth = pending.pop()
        remaining -= 1
        if remaining < 0 or depth > 8:
            return None
        if type(item) is str:
            remaining -= len(item)
        elif type(item) in {list, tuple, dict}:
            if len(item) > remaining:
                return None
            if type(item) is dict:
                if any(type(k) is not str for k in item):
                    return None
                pending.extend((k, depth + 1) for k in item)
                pending.extend((v, depth + 1) for v in item.values())
            else:
                pending.extend((v, depth + 1) for v in item)
        elif item is not None and type(item) not in {bool, int, float}:
            return None
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError):
        return None
    return hashlib.sha256(raw).hexdigest() if len(raw) <= budget else None


def loaded_startup_observation(config, context: dict) -> dict:
    """Observe loaded state at this callback; no reads, imports, probes or cache key."""
    policy = context['diagnostics']['startup_observation']
    budget = policy['maximum_metadata_bytes']
    result = {'protocol': policy['protocol'], 'status': 'INCOMPLETE', 'pytest_version': None,
              'root_sha256': None, 'root_matches_launch': False, 'config_path_sha256': None,
              'selected_ini_sha256': None, 'invocation_args_sha256': None,
              'registered_plugins_sha256': None, 'registered_plugin_count': 0,
              'environment': {}, 'plugin_code_identity_observed': False,
              'runtime_equivalence_proven': False, 'release_reuse_authority': False}
    component_fields = _STARTUP_COMPONENT_HASHES if policy['protocol'] == _STARTUP_PROTOCOLS[1] else ()
    result.update(dict.fromkeys(component_fields))
    for key in policy['environment_keys']:
        value = os.environ.get(key)
        result['environment'][key] = {'present': value is not None,
                                      'sha256': _startup_digest(value, budget) if value is not None else None}
    if config is None:
        return result
    try:
        version = vars(sys.modules['pytest']).get('__version__')
        if isinstance(version, str) and re.fullmatch(r'[0-9][A-Za-z0-9.+-]{0,63}', version):
            result['pytest_version'] = version
        result['root_sha256'] = digest(str(config.rootpath.resolve()))
        result['root_matches_launch'] = result['root_sha256'] == context['launch_root_sha256']
        if config.inipath is not None:
            result['config_path_sha256'] = digest(str(config.inipath.resolve()))
        result['selected_ini_sha256'] = _startup_digest({key: config.getini(key) for key in policy['ini_keys']}, budget)
        result['invocation_args_sha256'] = _startup_digest(config.invocation_params.args, budget)
        plugins = [(name, plugin) for name, plugin in config.pluginmanager.list_name_plugin() if plugin is not None]
        result['registered_plugin_count'] = len(plugins)
        if len(plugins) <= policy['maximum_plugins']:
            descriptors = []
            for registration, plugin in plugins:
                module = vars(plugin).get('__name__') if isinstance(plugin, ModuleType) else type(plugin).__module__
                descriptors.append([registration, module, type(plugin).__qualname__])
            result['registered_plugins_sha256'] = _startup_digest(descriptors, budget)
            if (component_fields and result['registered_plugins_sha256'] is not None
                    and all(type(value) is str for row in descriptors for value in row)):
                # Reuse this callback's one enumeration. Keep order in the original
                # identity; the secondary multiset is diagnostic, never equivalence.
                module_types = [row[1:] for row in descriptors]
                components = ([row[0] for row in descriptors], module_types, sorted(module_types))
                result.update({key: _startup_digest(value, budget)
                               for key, value in zip(component_fields, components)})
        if (result['pytest_version'] is not None
                and all(result[k] is not None for k in ['selected_ini_sha256', 'invocation_args_sha256', 'registered_plugins_sha256'])
                and all(result[k] is not None for k in component_fields)
                and all(not row['present'] or row['sha256'] is not None for row in result['environment'].values())):
            result['status'] = 'OBSERVED_METADATA_ONLY'
    except (AttributeError, KeyError, TypeError, ValueError, OSError, RuntimeError):
        result['status'] = 'INCOMPLETE'
    return result


def _valid_startup_observation(value, context: dict) -> bool:
    policy = context['diagnostics'].get('startup_observation')
    protocol = policy.get('protocol') if isinstance(policy, dict) else None
    component_fields = _STARTUP_COMPONENT_HASHES if protocol == _STARTUP_PROTOCOLS[1] else ()
    fields = {'protocol', 'status', 'pytest_version', 'root_sha256', 'root_matches_launch',
              'config_path_sha256', 'selected_ini_sha256', 'invocation_args_sha256',
              'registered_plugins_sha256', 'registered_plugin_count', 'environment',
              'plugin_code_identity_observed', 'runtime_equivalence_proven', 'release_reuse_authority'}
    fields.update(component_fields)
    if (protocol not in _STARTUP_PROTOCOLS or not isinstance(value, dict) or set(value) != fields
            or value['protocol'] != policy['protocol'] or value['status'] not in {'INCOMPLETE', 'OBSERVED_METADATA_ONLY'}
            or any(value[k] is not False for k in ['plugin_code_identity_observed', 'runtime_equivalence_proven', 'release_reuse_authority'])
            or type(value['root_matches_launch']) is not bool
            or type(value['registered_plugin_count']) is not int or value['registered_plugin_count'] < 0
            or not isinstance(value['environment'], dict) or set(value['environment']) != set(policy['environment_keys'])):
        return False
    if value['pytest_version'] is not None and (not isinstance(value['pytest_version'], str)
            or not re.fullmatch(r'[0-9][A-Za-z0-9.+-]{0,63}', value['pytest_version'])):
        return False
    for key in ['root_sha256', 'config_path_sha256', 'selected_ini_sha256', 'invocation_args_sha256', 'registered_plugins_sha256', *component_fields]:
        if value[key] is not None and (not isinstance(value[key], str) or not re.fullmatch(r'[a-f0-9]{64}', value[key])):
            return False
    if value['root_matches_launch'] != (value['root_sha256'] is not None and value['root_sha256'] == context.get('launch_root_sha256')):
        return False
    for row in value['environment'].values():
        if (not isinstance(row, dict) or set(row) != {'present', 'sha256'} or type(row['present']) is not bool
                or (row['sha256'] is not None and (not isinstance(row['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])))
                or (not row['present'] and row['sha256'] is not None)):
            return False
    return value['status'] != 'OBSERVED_METADATA_ONLY' or (
        value['pytest_version'] is not None and value['root_sha256'] is not None
        and value['registered_plugin_count'] <= policy['maximum_plugins']
        and all(value[k] is not None for k in ['selected_ini_sha256', 'invocation_args_sha256', 'registered_plugins_sha256'])
        and all(value[k] is not None for k in component_fields)
        and all(not row['present'] or row['sha256'] is not None for row in value['environment'].values()))


def _owned_probe_observation(value: object) -> dict:
    """Copy only the v1 builtin metadata shape; never render arbitrary objects."""
    def fields(item, keys):
        return (type(item) is dict and len(item) == len(keys)
                and all(type(key) is str for key in item) and set(item) == set(keys))

    def code(item):
        return item is None or (type(item) is int and -(2 ** 31) <= item <= 2 ** 32 - 1)

    outer = ("status", "observed_status", "returncode", "elapsed_seconds", "cleanup_confirmed", "cleanup_observation")
    nested = ("attempted", "method", "returncode", "child_returncode_before", "child_returncode_after", "error")
    statuses = {"missing", "exited", "available", "nonzero_exit", "invalid_output", "timed_out",
                "output_limit", "cleanup_unconfirmed", "launch_failed"}
    if not fields(value, outer):
        raise ValueError("invalid_owned_probe_observation")
    if (any(type(value[key]) is not str or len(value[key]) > 32 or value[key] not in statuses
            for key in ("status", "observed_status"))
            or not code(value["returncode"]) or type(value["cleanup_confirmed"]) is not bool):
        raise ValueError("invalid_owned_probe_observation")
    elapsed = value["elapsed_seconds"]
    if (type(elapsed) not in (int, float) or (type(elapsed) is int and elapsed.bit_length() > 64)
            or not math.isfinite(elapsed) or elapsed < 0):
        raise ValueError("invalid_owned_probe_observation")
    cleanup = value["cleanup_observation"]
    if (not fields(cleanup, nested) or type(cleanup["attempted"]) is not bool
            or (cleanup["method"] is not None and (type(cleanup["method"]) is not str
                or len(cleanup["method"]) > 8 or cleanup["method"] not in {"taskkill", "killpg"}))
            or any(not code(cleanup[key]) for key in ("returncode", "child_returncode_before", "child_returncode_after"))
            or value["returncode"] != cleanup["child_returncode_after"]):
        raise ValueError("invalid_owned_probe_observation")
    error = cleanup["error"]
    if error is not None:
        if (not fields(error, ("operation", "type", "code"))
                or type(error["operation"]) is not str or len(error["operation"]) > 9
                or error["operation"] not in {"terminate", "wait"}
                or type(error["type"]) is not str or len(error["type"]) > 80
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", error["type"]) is None or not code(error["code"])):
            raise ValueError("invalid_owned_probe_observation")
        error = dict(error)
    return {**value, "cleanup_observation": {**cleanup, "error": error}}


def _owned_probe_detail(properties: object, policy: dict) -> dict:
    """One bounded failure attachment. Foreign properties never enter output."""
    base = {"protocol": policy["protocol"]}
    rejected = {**base, "status": "rejected", "reason": "invalid_properties"}
    if type(properties) is not list or len(properties) > policy["maximum_properties"]:
        return rejected
    found = []
    for row in properties:
        if type(row) not in (tuple, list) or len(row) != 2 or type(row[0]) is not str:
            return rejected
        if row[0] == policy["protocol"]:
            found.append(row[1])
    if not found:
        return {**base, "status": "unavailable", "reason": "not_reported"}
    if len(found) != 1:
        return {**base, "status": "rejected", "reason": "duplicate_property"}
    try:
        observation = _owned_probe_observation(found[0])
    except ValueError:
        return {**base, "status": "rejected", "reason": "invalid_observation"}
    detail = {**base, "status": "captured", "observation": observation}
    if len(json.dumps(detail, ensure_ascii=True).encode("utf-8")) > policy["maximum_detail_bytes"]:
        return {**base, "status": "rejected", "reason": "oversized_observation"}
    return detail


def _valid_owned_probe_detail(detail: object, policy: dict) -> bool:
    if type(detail) is not dict or any(type(key) is not str for key in detail):
        return False
    status = detail.get("status")
    if detail.get("protocol") != policy["protocol"] or type(status) is not str:
        return False
    if status == "captured" and set(detail) == {"protocol", "status", "observation"}:
        expected = _owned_probe_detail([(policy["protocol"], detail["observation"])], policy)
        return expected["status"] == "captured" and expected == detail
    return (set(detail) == {"protocol", "status", "reason"}
            and type(detail["reason"]) is str
            and ((status == "unavailable" and detail["reason"] == "not_reported")
                 or (status == "rejected" and detail["reason"] in {
                     "invalid_properties", "duplicate_property", "invalid_observation", "oversized_observation"})))


def observe_journal(path: Path, context: dict, returncode: int | None = None) -> dict:
    """Read bounded metadata. Missing terminal evidence stays incomplete."""
    limit = context["diagnostics"]["maximum_failure_events"]
    line_limit = context["diagnostics"]["maximum_line_bytes"]
    preparation = context.get("candidate_preflight")
    case_limit = preparation["maximum_cases"] if preparation is not None else 0
    rows = []
    try:
        probe_policy = context["diagnostics"].get("owned_probe_failure_observation")
        if "owned_probe_failure_observation" in context["diagnostics"]:
            validate_owned_probe_failure_policy(probe_policy)
        with path.open("rb") as stream:
            for _ in range(limit + case_limit + 5):
                line = stream.readline(line_limit + 1)
                if not line:
                    break
                if len(line) > line_limit or not line.endswith(b"\n"):
                    raise ValueError("incomplete_test_journal")
                rows.append(json.loads(line))
            if stream.read(1):
                raise ValueError("test_journal_limit_exceeded")
        if (not rows or rows[0] != {"event": "launch", "context": context,
                                    "context_sha256": digest(context)}
                or any(not isinstance(r, dict) for r in rows[1:])
                or any(r.get("context_sha256") != digest(context) for r in rows[1:])):
            raise ValueError("test_journal_binding_mismatch")
        collected = finished = False
        startup = None
        for index, row in enumerate(rows[1:], 1):
            event = row.get("event")
            if finished:
                raise ValueError("test_journal_after_finish")
            if event == 'startup':
                if (startup is not None or collected or set(row) != {'event', 'context_sha256', 'observation'}
                        or not _valid_startup_observation(row['observation'], context)):
                    raise ValueError('invalid_pytest_startup_observation')
                startup = row['observation']
            elif event == "failure":
                fields = {"event", "context_sha256", "test_path", "case_name", "nodeid_sha256", "stage"}
                if "diagnostic_observation" in row:
                    if (probe_policy is None or row.get("test_path") not in probe_policy["producer_paths"]
                            or row.get("stage") not in {"setup", "call", "teardown"}
                            or not _valid_owned_probe_detail(row["diagnostic_observation"], probe_policy)):
                        raise ValueError("invalid_owned_probe_failure_detail")
                    fields.add("diagnostic_observation")
                if (set(row) != fields
                        or row["stage"] not in {"setup", "call", "teardown", "collection"}
                        or row["test_path"] not in context["test_paths"] + ["unmapped"]
                        or not re.fullmatch(r"[a-f0-9]{64}", str(row["nodeid_sha256"]))
                        or not re.fullmatch(r"(?:test_[a-zA-Z0-9_]{1,160}|collection_or_unmapped)", str(row["case_name"]))):
                    raise ValueError("invalid_test_failure_metadata")
            elif event == "collected":
                fields = {"event", "context_sha256", "count"} | ({"selected_functions"} if preparation is not None else set())
                if collected or set(row) != fields or type(row["count"]) is not int or row["count"] < 0:
                    raise ValueError("invalid_test_collection_metadata")
                if preparation is not None and (row["count"] > case_limit
                        or not isinstance(row["selected_functions"], list)
                        or len(row["selected_functions"]) > case_limit
                        or any(n not in preparation["selected_nodes"] for n in row["selected_functions"])):
                    raise ValueError("invalid_preflight_collection")
                collected = True
            elif event == "preflight_case":
                if (preparation is None
                        or set(row) != {"event", "context_sha256", "test_path", "case_name", "nodeid_sha256"}
                        or row["test_path"] + "::" + row["case_name"] not in preparation["selected_nodes"]
                        or not re.fullmatch(r"[a-f0-9]{64}", str(row["nodeid_sha256"]))):
                    raise ValueError("invalid_preflight_case_metadata")
            elif event == "finish":
                if (set(row) != {"event", "context_sha256", "exitstatus", "phase_counts", "failure_events", "omitted_failure_events"}
                        or any(type(row[k]) is not int for k in ["exitstatus", "failure_events", "omitted_failure_events"])
                        or row["failure_events"] < 0
                        or row["omitted_failure_events"] != max(0, row["failure_events"] - limit)
                        or row["failure_events"] - row["omitted_failure_events"] != sum(r.get("event") == "failure" for r in rows[1:])
                        or not isinstance(row["phase_counts"], dict)
                        or any(not re.fullmatch(r"(?:setup|call|teardown):(?:passed|failed|skipped)", k)
                               or type(v) is not int or v < 0 for k, v in row["phase_counts"].items())):
                    raise ValueError("invalid_test_finish_metadata")
                finished = True
            else:
                raise ValueError("invalid_test_journal_event")
        terminal = rows[-1]
        complete = (terminal.get("event") == "finish"
                    and (context['diagnostics'].get('startup_observation') is None or startup is not None)
                    and returncode is not None and terminal.get("exitstatus") == returncode
                    and (returncode != 0 or terminal.get("failure_events") == 0))
        return {"status": "FINISHED" if complete else "INCOMPLETE", "path": str(path),
                "context_sha256": digest(context), "journal_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "failures": [r for r in rows if r.get("event") == "failure"],
                "collection": next((r for r in rows if r.get("event") == "collected"), None),
                "completed_preflight_cases": [r for r in rows if r.get("event") == "preflight_case"],
                "startup_observation": startup,
                "terminal": terminal if complete else None, "release_reuse_authority": False}
    except (OSError, ValueError, TypeError, KeyError):
        return {"status": "UNAVAILABLE_OR_INCOMPLETE", "path": str(path),
                "context_sha256": digest(context), "release_reuse_authority": False}


class _FailureJournal:
    def __init__(self, path: Path, context: dict):
        self.context = context
        self.path = path
        self.limit = context["diagnostics"]["maximum_failure_events"]
        self.failures = 0
        self.counts: Counter[str] = Counter()
        self.preparation = context.get("candidate_preflight")
        self.completed_cases = 0
        self.startup_recorded = False

    def write(self, **row: object) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as out:
                out.write(json.dumps({**row, "context_sha256": digest(self.context)}) + "\n")
                out.flush()
                os.fsync(out.fileno())
        except OSError:
            raise RuntimeError("test_journal_write_failed") from None

    def case_fields(self, nodeid: str) -> dict:
        path, _, rest = nodeid.partition("::")
        path = path if path in self.context["test_paths"] else "unmapped"
        name = rest.split("[", 1)[0].split("::")[-1]
        name = name if re.fullmatch(r"test_[a-zA-Z0-9_]{1,160}", name) else "collection_or_unmapped"
        return {"test_path": path, "case_name": name,
                "nodeid_sha256": hashlib.sha256(nodeid.encode("utf-8")).hexdigest()}

    def failure(self, nodeid: str, stage: str, properties: object = None) -> None:
        self.failures += 1
        if self.failures <= self.limit:
            fields = self.case_fields(nodeid)
            policy = self.context["diagnostics"].get("owned_probe_failure_observation")
            if (policy is not None and fields["test_path"] in policy["producer_paths"]
                    and stage in {"setup", "call", "teardown"}):
                fields["diagnostic_observation"] = _owned_probe_detail(properties, policy)
            self.write(event="failure", **fields, stage=stage)

    def pytest_collection_finish(self, session):
        self.startup(getattr(session, 'config', None))
        if self.preparation is None:
            self.write(event="collected", count=len(session.items))
            return
        if len(session.items) > self.preparation["maximum_cases"]:
            raise RuntimeError("candidate_preflight_case_limit")
        functions = set()
        for item in session.items:
            fields = self.case_fields(item.nodeid)
            functions.add(fields["test_path"] + "::" + fields["case_name"])
        self.write(event="collected", count=len(session.items), selected_functions=sorted(functions))

    def pytest_collectreport(self, report):
        if report.failed:
            self.failure(report.nodeid, "collection")

    def pytest_runtest_logreport(self, report):
        self.counts[report.when + ":" + report.outcome] += 1
        if report.failed:
            self.failure(report.nodeid, report.when, getattr(report, "user_properties", []))
        if self.preparation is not None and report.when == "call" and report.passed:
            self.completed_cases += 1
            if self.completed_cases > self.preparation["maximum_cases"]:
                raise RuntimeError("candidate_preflight_case_limit")
            self.write(event="preflight_case", **self.case_fields(report.nodeid))

    def pytest_sessionfinish(self, session, exitstatus):
        self.startup(session.config if session is not None else None)
        self.write(event="finish", exitstatus=int(exitstatus), phase_counts=dict(self.counts),
                   failure_events=self.failures, omitted_failure_events=max(0, self.failures - self.limit))

    def startup(self, config):
        if not self.startup_recorded and self.context['diagnostics'].get('startup_observation') is not None:
            self.write(event='startup', observation=loaded_startup_observation(config, self.context))
            self.startup_recorded = True


def pytest_configure(config):
    # Explicit -p loading is mandatory for the owned launcher. Never inherit a
    # caller-supplied plugin context or silently synthesize one after startup.
    try:
        expected = os.environ[CONTEXT_ENV]
        path = Path(os.environ[JOURNAL_ENV])
        limit = execution_contract()["diagnostics"]["maximum_line_bytes"]
        with path.open("rb") as stream:
            header = json.loads(stream.readline(limit + 1))
        context = header["context"]
        if digest(context) != expected:
            raise ValueError("invalid_launch")
        if observe_journal(path, context)["status"] != "INCOMPLETE":
            raise ValueError("invalid_launch")
    except (OSError, KeyError, ValueError, TypeError):
        raise RuntimeError("owned_test_launch_context_required") from None
    config.pluginmanager.register(_FailureJournal(path, context), "sage-owned-failure-journal")
