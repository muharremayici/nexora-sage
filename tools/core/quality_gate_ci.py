from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PR_MATRIX_RECEIPT_KIND = "nexora_sage_pr_matrix_receipt_v1"


def current_git_tree_sha(root: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    tree_sha = result.stdout.strip().lower()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", tree_sha):
        raise ValueError("Unable to resolve the exact checked-out Git tree identity")
    return tree_sha


def supported_python_versions(root: Path = ROOT) -> list[str]:
    payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    versions = (
        payload.get("tool", {})
        .get("nexora_sage", {})
        .get("distribution", {})
        .get("tested_python_versions", [])
    )
    normalized = [str(version).strip() for version in versions if str(version).strip()]
    if not normalized or len(normalized) != len(set(normalized)):
        raise ValueError("Declared tested Python versions must be a non-empty unique list")
    return normalized


def load_ci_execution_policy(root: Path = ROOT) -> dict[str, Any]:
    path = root / "config" / "installation_preflight_contract.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    policy = payload.get("ci_execution_policy")
    if not isinstance(policy, dict):
        raise ValueError("Installation preflight contract is missing ci_execution_policy")
    validate_ci_execution_policy(policy, root=root)
    return policy


def validate_ci_execution_policy(policy: dict[str, Any], *, root: Path = ROOT) -> None:
    if policy.get("contract") != "pr_matrix_main_reference_v1":
        raise ValueError("Unknown CI execution policy contract")
    if policy.get("workflow_path") != ".github/workflows/quality-gate.yml":
        raise ValueError("CI execution policy must own the existing quality-gate workflow")
    if policy.get("target_branch") != "main":
        raise ValueError("CI execution policy target branch must be main")
    if policy.get("runtime_versions_source") != (
        "pyproject.toml#tool.nexora_sage.distribution.tested_python_versions"
    ):
        raise ValueError("CI runtime version source must remain pyproject.toml")
    if str(policy.get("reference_python_version") or "") not in supported_python_versions(root):
        raise ValueError("CI reference Python version must be in the supported runtime matrix")
    if policy.get("push_association_failure_policy") != "full_matrix_with_explicit_reason":
        raise ValueError("CI push-association failure must fall back to explicit full-matrix proof")

    tiers = policy.get("event_tiers")
    expected_tiers = {
        "pull_request_main": "full_matrix",
        "associated_main_push": "reference_runtime",
        "direct_main_push": "full_matrix",
        "unknown": "block",
    }
    if not isinstance(tiers, dict) or set(tiers) != set(expected_tiers):
        raise ValueError("CI execution policy must declare every governed event tier")
    tier_names: list[str] = []
    for event_class, execution in expected_tiers.items():
        row = tiers.get(event_class)
        tier = str(row.get("tier") or "") if isinstance(row, dict) else ""
        if (
            not isinstance(row, dict)
            or not re.fullmatch(r"[a-z][a-z0-9_]*", tier)
            or row.get("execution") != execution
        ):
            raise ValueError(f"CI event tier semantics drifted: {event_class}")
        tier_names.append(tier)
    if len(tier_names) != len(set(tier_names)):
        raise ValueError("CI event tier names must be unique")
    if tiers["associated_main_push"].get("requires_verified_pr_matrix_receipt") is not True:
        raise ValueError("Reference-runtime main verification requires a PR matrix receipt")

    commands = policy.get("commands")
    required_command_names = {
        "compile",
        "cli_smoke",
        "full_suite",
        "target_prepare",
        "target_init",
        "target_doctor",
    }
    if not isinstance(commands, dict) or set(commands) != required_command_names:
        raise ValueError("CI execution policy must declare every governed command")
    if any(
        not isinstance(command, str) or not command.strip() or "\n" in command
        for command in commands.values()
    ):
        raise ValueError("CI execution commands must be non-empty single-line strings")

    cache = policy.get("cache")
    if not isinstance(cache, dict):
        raise ValueError("CI execution policy cache contract is missing")
    if cache.get("pip_dependency_paths") != ["pyproject.toml", "requirements.txt"]:
        raise ValueError("CI pip cache identity must include both dependency manifests")
    if cache.get("npm_dependency_path") != "tools/engines/package-lock.json":
        raise ValueError("CI npm cache identity must be the engine lockfile")
    if cache.get("cancel_in_progress") != "pull_request_only":
        raise ValueError("CI cancellation may cancel only superseded pull-request runs")


def resolve_event_tier(
    event_name: str,
    event_payload: dict[str, Any],
    *,
    associated_pulls: list[dict[str, Any]] | None = None,
    matrix_receipt_verified: bool = False,
    policy: dict[str, Any] | None = None,
) -> str:
    selected_policy = policy or load_ci_execution_policy()
    tiers = selected_policy["event_tiers"]
    target_branch = str(selected_policy["target_branch"])
    normalized_event = str(event_name or "").strip()
    if normalized_event == "pull_request":
        pull_request = event_payload.get("pull_request")
        base = pull_request.get("base") if isinstance(pull_request, dict) else None
        if not isinstance(base, dict) or base.get("ref") != target_branch:
            raise ValueError("Pull-request event does not target the governed branch")
        return str(tiers["pull_request_main"]["tier"])
    if normalized_event != "push":
        raise ValueError(f"Unsupported CI event: {normalized_event or 'missing'}")
    if event_payload.get("ref") != f"refs/heads/{target_branch}":
        raise ValueError("Push event does not target the governed branch")
    after = str(event_payload.get("after") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", after):
        raise ValueError("Push event is missing an exact 40-character source commit")
    if associated_pulls is None:
        raise ValueError("Push classification requires associated pull-request evidence")
    if not isinstance(associated_pulls, list):
        raise ValueError("Associated pull-request evidence must be a list")

    for row in associated_pulls:
        if not isinstance(row, dict):
            raise ValueError("Associated pull-request evidence contains an invalid row")
        base = row.get("base")
        if (
            row.get("merged_at")
            and str(row.get("merge_commit_sha") or "").lower() == after
            and isinstance(base, dict)
            and base.get("ref") == target_branch
        ):
            if matrix_receipt_verified:
                return str(tiers["associated_main_push"]["tier"])
            return str(tiers["direct_main_push"]["tier"])
    return str(tiers["direct_main_push"]["tier"])


def _receipt_identity(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def build_pr_matrix_receipt(
    *,
    pull_request_number: int,
    pull_request_head_sha: str,
    event_sha: str,
    source_tree_sha: str,
    repository: str,
    workflow_run_id: int,
    workflow_run_attempt: int,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_policy = policy or load_ci_execution_policy()
    core = {
        "meta": {"kind": PR_MATRIX_RECEIPT_KIND, "version": "v1"},
        "source": {
            "pull_request_number": int(pull_request_number),
            "pull_request_head_sha": str(pull_request_head_sha).lower(),
            "event_sha": str(event_sha).lower(),
            "source_tree_sha": str(source_tree_sha).lower(),
            "repository": str(repository),
            "workflow_path": str(selected_policy["workflow_path"]),
            "workflow_run_id": int(workflow_run_id),
            "workflow_run_attempt": int(workflow_run_attempt),
        },
        "proof": {
            "python_versions": supported_python_versions(),
            "suite_command": str(selected_policy["commands"]["full_suite"]),
            "matrix_result": "success",
        },
    }
    validate_pr_matrix_receipt_shape(core, require_identity=False)
    return {**core, "receipt_sha256": _receipt_identity(core)}


def validate_pr_matrix_receipt_shape(
    payload: dict[str, Any],
    *,
    require_identity: bool = True,
) -> None:
    meta = payload.get("meta")
    source = payload.get("source")
    proof = payload.get("proof")
    if not isinstance(meta, dict) or meta.get("kind") != PR_MATRIX_RECEIPT_KIND:
        raise ValueError("PR matrix receipt kind is invalid")
    if not isinstance(source, dict) or not isinstance(proof, dict):
        raise ValueError("PR matrix receipt sections are missing")
    for name in ("pull_request_head_sha", "event_sha", "source_tree_sha"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(source.get(name) or "")):
            raise ValueError(f"PR matrix receipt {name} is invalid")
    if not isinstance(source.get("pull_request_number"), int) or source["pull_request_number"] < 1:
        raise ValueError("PR matrix receipt pull-request number is invalid")
    for name in ("workflow_run_id", "workflow_run_attempt"):
        if not isinstance(source.get(name), int) or source[name] < 1:
            raise ValueError(f"PR matrix receipt {name} is invalid")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(source.get("repository") or "")):
        raise ValueError("PR matrix receipt repository is invalid")
    if proof.get("matrix_result") != "success":
        raise ValueError("PR matrix receipt does not represent a successful matrix")
    if require_identity:
        identity = str(payload.get("receipt_sha256") or "")
        core = {key: value for key, value in payload.items() if key != "receipt_sha256"}
        if not re.fullmatch(r"[0-9a-f]{64}", identity) or identity != _receipt_identity(core):
            raise ValueError("PR matrix receipt identity is invalid")


def verify_pr_matrix_receipt(
    payload: dict[str, Any],
    *,
    pull_request_number: int,
    pull_request_head_sha: str,
    source_tree_sha: str,
    repository: str,
    workflow_run_id: int,
    workflow_run_attempt: int,
    policy: dict[str, Any] | None = None,
) -> None:
    selected_policy = policy or load_ci_execution_policy()
    validate_pr_matrix_receipt_shape(payload)
    source = payload["source"]
    proof = payload["proof"]
    expected = {
        "pull_request_number": int(pull_request_number),
        "pull_request_head_sha": str(pull_request_head_sha).lower(),
        "source_tree_sha": str(source_tree_sha).lower(),
        "repository": str(repository),
        "workflow_path": str(selected_policy["workflow_path"]),
        "workflow_run_id": int(workflow_run_id),
        "workflow_run_attempt": int(workflow_run_attempt),
    }
    mismatches = [name for name, value in expected.items() if source.get(name) != value]
    if mismatches:
        raise ValueError("PR matrix receipt binding mismatch: " + ", ".join(mismatches))
    if proof.get("python_versions") != supported_python_versions():
        raise ValueError("PR matrix receipt runtime coverage is incomplete")
    if proof.get("suite_command") != selected_policy["commands"]["full_suite"]:
        raise ValueError("PR matrix receipt suite command drifted")


def suite_execution_count(
    tier: str,
    *,
    policy: dict[str, Any] | None = None,
    versions: list[str] | None = None,
) -> int:
    selected_policy = policy or load_ci_execution_policy()
    selected_versions = versions or supported_python_versions()
    row = next(
        (item for item in selected_policy["event_tiers"].values() if item.get("tier") == tier),
        None,
    )
    if not isinstance(row, dict):
        raise ValueError(f"Unknown CI tier: {tier}")
    if row.get("execution") == "full_matrix":
        return len(selected_versions)
    if row.get("execution") == "reference_runtime":
        return 1
    if row.get("execution") == "block":
        return 0
    raise ValueError(f"Unknown CI execution mode: {row.get('execution')}")


def normal_pr_main_journey(policy: dict[str, Any] | None = None) -> dict[str, int]:
    selected_policy = policy or load_ci_execution_policy()
    versions = supported_python_versions()
    pr_tier = selected_policy["event_tiers"]["pull_request_main"]["tier"]
    main_tier = selected_policy["event_tiers"]["associated_main_push"]["tier"]
    pr_count = suite_execution_count(pr_tier, policy=selected_policy, versions=versions)
    main_count = suite_execution_count(main_tier, policy=selected_policy, versions=versions)
    return {
        "pull_request_full_suite_executions": pr_count,
        "main_full_suite_executions": main_count,
        "total_full_suite_executions": pr_count + main_count,
        "full_matrix_executions": 1,
    }


def _workflow_job_block(workflow_text: str, job_id: str) -> str:
    marker = f"  {job_id}:\n"
    start = workflow_text.find(marker)
    if start < 0:
        return ""
    end_match = re.search(r"(?m)^  [A-Za-z0-9_-]+:\s*$", workflow_text[start + len(marker) :])
    if end_match is None:
        return workflow_text[start:]
    return workflow_text[start : start + len(marker) + end_match.start()]


def validate_workflow_text(
    workflow_text: str,
    *,
    policy: dict[str, Any] | None = None,
    versions: list[str] | None = None,
) -> dict[str, Any]:
    selected_policy = policy or load_ci_execution_policy()
    selected_versions = versions or supported_python_versions()
    tiers = selected_policy["event_tiers"]
    commands = selected_policy["commands"]
    version_literal = json.dumps(selected_versions)
    classifier_block = _workflow_job_block(workflow_text, "ci-tier")
    matrix_block = _workflow_job_block(workflow_text, "python-runtime-compatibility")
    receipt_block = _workflow_job_block(workflow_text, "pr-matrix-receipt")
    reference_block = _workflow_job_block(workflow_text, "post-merge-reference")
    required_tokens = [
        "pull_request:\n    branches: [main]",
        "push:\n    branches: [main]",
        "pull-requests: read",
        "actions: read",
        "python -B tools/resolve_quality_gate_ci_tier.py",
        "python -B tools/create_quality_gate_pr_matrix_receipt.py",
        "actions/upload-artifact@v4",
        "name: nexora-sage-pr-matrix-receipt",
        str(tiers["pull_request_main"]["tier"]),
        str(tiers["associated_main_push"]["tier"]),
        str(tiers["direct_main_push"]["tier"]),
        f"python-version: {version_literal}",
        f'python-version: "{selected_policy["reference_python_version"]}"',
        "cache: pip",
        "cache-dependency-path: |",
        "cache: npm",
        "cache-dependency-path: tools/engines/package-lock.json",
        f'node-version: "{selected_policy["node_version"]}"',
        "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
        "ref: ${{ github.sha }}",
        f"if: matrix.python-version == '{selected_policy['reference_python_version']}'",
        *selected_policy["cache"]["pip_dependency_paths"],
        *[str(command) for command in commands.values()],
    ]
    missing_tokens = [token for token in required_tokens if token not in workflow_text]
    forbidden_tokens = [
        token for token in ("--collect-only", "continue-on-error: true") if token in workflow_text
    ]
    full_suite_occurrences = workflow_text.count(str(commands["full_suite"]))
    matrix_literal_occurrences = workflow_text.count(f"python-version: {version_literal}")
    target_lifecycle_occurrences = {
        name: workflow_text.count(str(commands[name]))
        for name in ("target_prepare", "target_init", "target_doctor")
    }
    job_bindings = {
        "classifier": bool(classifier_block)
        and "python -B tools/resolve_quality_gate_ci_tier.py" in classifier_block,
        "matrix": bool(matrix_block)
        and str(tiers["pull_request_main"]["tier"]) in matrix_block
        and str(tiers["direct_main_push"]["tier"]) in matrix_block
        and f"python-version: {version_literal}" in matrix_block
        and matrix_block.count(str(commands["full_suite"])) == 1
        and all(str(commands[name]) in matrix_block for name in target_lifecycle_occurrences),
        "receipt": bool(receipt_block)
        and str(tiers["pull_request_main"]["tier"]) in receipt_block
        and "needs.python-runtime-compatibility.result == 'success'" in receipt_block
        and "python -B tools/create_quality_gate_pr_matrix_receipt.py" in receipt_block
        and "actions/upload-artifact@v4" in receipt_block
        and "name: nexora-sage-pr-matrix-receipt" in receipt_block,
        "reference": bool(reference_block)
        and str(tiers["associated_main_push"]["tier"]) in reference_block
        and f'python-version: "{selected_policy["reference_python_version"]}"' in reference_block
        and f"python-version: {version_literal}" not in reference_block
        and reference_block.count(str(commands["full_suite"])) == 1
        and all(str(commands[name]) in reference_block for name in target_lifecycle_occurrences),
    }
    cache_occurrences = {
        "pip": workflow_text.count("cache: pip"),
        "npm": workflow_text.count("cache: npm"),
    }
    node_version_occurrences = workflow_text.count(
        f'node-version: "{selected_policy["node_version"]}"'
    )
    passed = (
        not missing_tokens
        and not forbidden_tokens
        and full_suite_occurrences == 2
        and matrix_literal_occurrences == 1
        and all(count == 2 for count in target_lifecycle_occurrences.values())
        and all(job_bindings.values())
        and cache_occurrences == {"pip": 2, "npm": 2}
        and node_version_occurrences == 2
        and workflow_text.count("ref: ${{ github.sha }}") == 4
    )
    return {
        "passed": passed,
        "missing_tokens": missing_tokens,
        "forbidden_tokens": forbidden_tokens,
        "full_suite_command_occurrences": full_suite_occurrences,
        "full_matrix_literal_occurrences": matrix_literal_occurrences,
        "target_lifecycle_command_occurrences": target_lifecycle_occurrences,
        "job_bindings": job_bindings,
        "cache_occurrences": cache_occurrences,
        "node_version_occurrences": node_version_occurrences,
        "exact_checkout_occurrences": workflow_text.count("ref: ${{ github.sha }}"),
        "normal_pr_main_journey": normal_pr_main_journey(selected_policy),
        "claim_boundary": selected_policy.get("claim_boundary"),
    }


def validate_documentation_text(
    documentation_text: str,
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_policy = policy or load_ci_execution_policy()
    journey = normal_pr_main_journey(selected_policy)
    versions = supported_python_versions()
    required_tokens = [
        "config/installation_preflight_contract.json:ci_execution_policy",
        *versions,
        f"Pull-request full-suite executions: `{journey['pull_request_full_suite_executions']}`",
        f"Main reference full-suite executions: `{journey['main_full_suite_executions']}`",
        f"Normal PR-to-main total: `{journey['total_full_suite_executions']}`",
        f"Full-matrix executions: `{journey['full_matrix_executions']}`",
        "direct `main` push",
        "Content-addressed cross-workflow receipt reuse is implemented",
    ]
    normalized_documentation = re.sub(r"\s+", " ", documentation_text)
    missing_tokens = [
        token
        for token in required_tokens
        if re.sub(r"\s+", " ", token) not in normalized_documentation
    ]
    return {
        "passed": not missing_tokens,
        "missing_tokens": missing_tokens,
        "normal_pr_main_journey": journey,
    }
