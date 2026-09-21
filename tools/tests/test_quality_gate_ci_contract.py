from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from tools import create_quality_gate_pr_matrix_receipt as receipt_creator
from tools import resolve_quality_gate_ci_tier as tier_resolver
from tools.core.quality_gate_ci import (
    ROOT,
    build_pr_matrix_receipt,
    load_ci_execution_policy,
    normal_pr_main_journey,
    resolve_event_tier,
    suite_execution_count,
    supported_python_versions,
    validate_documentation_text,
    validate_workflow_text,
    verify_pr_matrix_receipt,
)


SHA = "a" * 40
HEAD_SHA = "b" * 40
EVENT_SHA = "c" * 40
TREE_SHA = "d" * 40


def _pull_request_event() -> dict[str, object]:
    return {"pull_request": {"base": {"ref": "main"}}}


def _push_event() -> dict[str, object]:
    return {
        "ref": "refs/heads/main",
        "after": SHA,
        "repository": {"full_name": "example/nexora-sage"},
    }


def _merged_pull_request() -> dict[str, object]:
    return {
        "number": 42,
        "merged_at": "2026-09-19T00:00:00Z",
        "merge_commit_sha": SHA,
        "base": {"ref": "main"},
        "head": {"sha": HEAD_SHA},
    }


def _receipt(*, tree_sha: str = TREE_SHA) -> dict[str, object]:
    return build_pr_matrix_receipt(
        pull_request_number=42,
        pull_request_head_sha=HEAD_SHA,
        event_sha=EVENT_SHA,
        source_tree_sha=tree_sha,
        repository="example/nexora-sage",
        workflow_run_id=123,
        workflow_run_attempt=2,
    )


def _rehash_receipt(receipt: dict[str, object]) -> None:
    core = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    receipt["receipt_sha256"] = hashlib.sha256(encoded).hexdigest()


def _receipt_archive(receipt: dict[str, object]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(
            tier_resolver.RECEIPT_FILE_NAME,
            json.dumps(receipt, sort_keys=True),
        )
    return buffer.getvalue()


def test_pull_request_runs_full_supported_runtime_matrix() -> None:
    policy = load_ci_execution_policy()
    tier = resolve_event_tier("pull_request", _pull_request_event(), policy=policy)

    assert tier == "pull_request_full_matrix"
    assert suite_execution_count(tier, policy=policy) == len(supported_python_versions()) == 4


def test_exact_merged_main_push_runs_one_reference_suite() -> None:
    policy = load_ci_execution_policy()
    tier = resolve_event_tier(
        "push",
        _push_event(),
        associated_pulls=[_merged_pull_request()],
        matrix_receipt_verified=True,
        policy=policy,
    )

    assert tier == "associated_main_push_reference"
    assert suite_execution_count(tier, policy=policy) == 1


def test_associated_main_push_without_verified_receipt_runs_full_matrix() -> None:
    tier = resolve_event_tier(
        "push",
        _push_event(),
        associated_pulls=[_merged_pull_request()],
    )

    assert tier == "direct_main_push_full_matrix"
    assert suite_execution_count(tier) == 4


def test_direct_main_push_falls_back_to_full_matrix() -> None:
    policy = load_ci_execution_policy()
    tier = resolve_event_tier(
        "push",
        _push_event(),
        associated_pulls=[],
        policy=policy,
    )

    assert tier == "direct_main_push_full_matrix"
    assert suite_execution_count(tier, policy=policy) == 4


@pytest.mark.parametrize(
    ("event_name", "event_payload", "associated_pulls", "message"),
    [
        ("workflow_dispatch", {}, None, "Unsupported CI event"),
        ("pull_request", {"pull_request": {"base": {"ref": "dev"}}}, None, "governed branch"),
        ("push", _push_event(), None, "requires associated pull-request evidence"),
        ("push", {**_push_event(), "ref": "refs/heads/dev"}, [], "governed branch"),
    ],
)
def test_unknown_or_incomplete_event_evidence_fails_closed(
    event_name: str,
    event_payload: dict[str, object],
    associated_pulls: list[dict[str, object]] | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_event_tier(
            event_name,
            event_payload,
            associated_pulls=associated_pulls,
        )


def test_push_api_classification_requires_token_before_network() -> None:
    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        tier_resolver._associated_pull_requests(_push_event(), token="")


def test_push_api_evidence_is_content_bound_without_real_network(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps([_merged_pull_request()]).encode("utf-8")

    def fake_urlopen(request, *, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(tier_resolver.urllib.request, "urlopen", fake_urlopen)

    rows = tier_resolver._associated_pull_requests(_push_event(), token="secret")

    assert rows == [_merged_pull_request()]
    assert captured["url"] == (
        f"https://api.github.com/repos/example/nexora-sage/commits/{SHA}/pulls?per_page=100"
    )
    assert captured["authorization"] == "Bearer secret"
    assert captured["timeout"] == 20.0


def test_resolver_cli_writes_only_the_governed_tier(
    monkeypatch,
    tmp_path: Path,
) -> None:
    event_path = tmp_path / "event.json"
    output_path = tmp_path / "github-output.txt"
    event_path.write_text(json.dumps(_push_event()), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setattr(
        tier_resolver,
        "_associated_pull_requests",
        lambda *_args, **_kwargs: [_merged_pull_request()],
    )
    monkeypatch.setattr(tier_resolver, "current_git_tree_sha", lambda: TREE_SHA)
    monkeypatch.setattr(
        tier_resolver,
        "_verified_pr_matrix_receipt",
        lambda *_args, **_kwargs: _receipt(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resolve_quality_gate_ci_tier.py",
            "--event-name",
            "push",
            "--event-path",
            str(event_path),
            "--github-output",
            str(output_path),
        ],
    )

    assert tier_resolver.main() == 0
    assert output_path.read_text(encoding="utf-8") == (
        "tier=associated_main_push_reference\n"
        "reason=verified_content_bound_pr_matrix_reference\n"
    )


def test_resolver_cli_missing_token_falls_back_to_full_matrix(
    monkeypatch,
    tmp_path: Path,
) -> None:
    event_path = tmp_path / "event.json"
    output_path = tmp_path / "github-output.txt"
    event_path.write_text(json.dumps(_push_event()), encoding="utf-8")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resolve_quality_gate_ci_tier.py",
            "--event-name",
            "push",
            "--event-path",
            str(event_path),
            "--github-output",
            str(output_path),
        ],
    )

    assert tier_resolver.main() == 0
    assert output_path.read_text(encoding="utf-8") == (
        "tier=direct_main_push_full_matrix\n"
        "reason=association_evidence_unavailable_full_matrix_fallback\n"
    )


def test_resolver_cli_association_api_failure_falls_back_to_full_matrix(
    monkeypatch,
    tmp_path: Path,
) -> None:
    event_path = tmp_path / "event.json"
    output_path = tmp_path / "github-output.txt"
    event_path.write_text(json.dumps(_push_event()), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setattr(
        tier_resolver,
        "_associated_pull_requests",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("API unavailable")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resolve_quality_gate_ci_tier.py",
            "--event-name",
            "push",
            "--event-path",
            str(event_path),
            "--github-output",
            str(output_path),
        ],
    )

    assert tier_resolver.main() == 0
    assert output_path.read_text(encoding="utf-8") == (
        "tier=direct_main_push_full_matrix\n"
        "reason=association_evidence_unavailable_full_matrix_fallback\n"
    )


def test_resolver_cli_unavailable_receipt_falls_back_to_full_matrix(
    monkeypatch,
    tmp_path: Path,
) -> None:
    event_path = tmp_path / "event.json"
    output_path = tmp_path / "github-output.txt"
    event_path.write_text(json.dumps(_push_event()), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setattr(
        tier_resolver,
        "_associated_pull_requests",
        lambda *_args, **_kwargs: [_merged_pull_request()],
    )
    monkeypatch.setattr(tier_resolver, "current_git_tree_sha", lambda: TREE_SHA)
    monkeypatch.setattr(
        tier_resolver,
        "_verified_pr_matrix_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("cancelled")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resolve_quality_gate_ci_tier.py",
            "--event-name",
            "push",
            "--event-path",
            str(event_path),
            "--github-output",
            str(output_path),
        ],
    )

    assert tier_resolver.main() == 0
    assert output_path.read_text(encoding="utf-8") == (
        "tier=direct_main_push_full_matrix\n"
        "reason=pr_matrix_receipt_unavailable_full_matrix_fallback\n"
    )


def test_receipt_verifier_rejects_tree_mismatch_and_missing_runtime_cell() -> None:
    receipt = _receipt()
    with pytest.raises(ValueError, match="source_tree_sha"):
        verify_pr_matrix_receipt(
            receipt,
            pull_request_number=42,
            pull_request_head_sha=HEAD_SHA,
            source_tree_sha="e" * 40,
            repository="example/nexora-sage",
            workflow_run_id=123,
            workflow_run_attempt=2,
        )

    proof = receipt["proof"]
    assert isinstance(proof, dict)
    proof["python_versions"] = ["3.11", "3.12", "3.13"]
    _rehash_receipt(receipt)
    with pytest.raises(ValueError, match="runtime coverage"):
        verify_pr_matrix_receipt(
            receipt,
            pull_request_number=42,
            pull_request_head_sha=HEAD_SHA,
            source_tree_sha=TREE_SHA,
            repository="example/nexora-sage",
            workflow_run_id=123,
            workflow_run_attempt=2,
        )


def test_receipt_creator_cli_writes_a_verifiable_exact_tree_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / tier_resolver.RECEIPT_FILE_NAME
    monkeypatch.setattr(receipt_creator, "current_git_tree_sha", lambda: TREE_SHA)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "create_quality_gate_pr_matrix_receipt.py",
            "--output",
            str(output),
            "--pull-request-number",
            "42",
            "--pull-request-head-sha",
            HEAD_SHA,
            "--event-sha",
            EVENT_SHA,
            "--repository",
            "example/nexora-sage",
            "--workflow-run-id",
            "123",
            "--workflow-run-attempt",
            "2",
        ],
    )

    assert receipt_creator.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    verify_pr_matrix_receipt(
        payload,
        pull_request_number=42,
        pull_request_head_sha=HEAD_SHA,
        source_tree_sha=TREE_SHA,
        repository="example/nexora-sage",
        workflow_run_id=123,
        workflow_run_attempt=2,
    )


@pytest.mark.parametrize("conclusion", ["cancelled", "failure", None])
def test_cancelled_failed_or_bypassed_matrix_has_no_reusable_receipt(conclusion) -> None:
    def fetch_json(url: str, *, token: str):
        if "/runs?" in url:
            return {
                "workflow_runs": [
                    {
                        "id": 123,
                        "run_attempt": 2,
                        "event": "pull_request",
                        "conclusion": conclusion,
                        "head_sha": HEAD_SHA,
                    }
                ]
            }
        return {"artifacts": []}

    with pytest.raises(ValueError, match="No verified PR matrix receipt"):
        tier_resolver._verified_pr_matrix_receipt(
            _push_event(),
            [_merged_pull_request()],
            token="secret",
            source_tree_sha=TREE_SHA,
            policy=load_ci_execution_policy(),
            fetch_json=fetch_json,
            fetch_archive=lambda *_args, **_kwargs: b"",
        )


def test_successful_workflow_without_receipt_artifact_cannot_reduce_main_matrix() -> None:
    def fetch_json(url: str, *, token: str):
        if "/runs?" in url:
            return {
                "workflow_runs": [
                    {
                        "id": 123,
                        "run_attempt": 2,
                        "event": "pull_request",
                        "conclusion": "success",
                        "head_sha": HEAD_SHA,
                    }
                ]
            }
        return {"artifacts": []}

    with pytest.raises(ValueError, match="No verified PR matrix receipt"):
        tier_resolver._verified_pr_matrix_receipt(
            _push_event(),
            [_merged_pull_request()],
            token="secret",
            source_tree_sha=TREE_SHA,
            policy=load_ci_execution_policy(),
            fetch_json=fetch_json,
            fetch_archive=lambda *_args, **_kwargs: b"",
        )


def test_successful_matrix_receipt_is_reused_only_for_identical_tree() -> None:
    receipt = _receipt()

    def fetch_json(url: str, *, token: str):
        if "/runs?" in url:
            return {
                "workflow_runs": [
                    {
                        "id": 123,
                        "run_attempt": 2,
                        "event": "pull_request",
                        "conclusion": "success",
                        "head_sha": HEAD_SHA,
                    }
                ]
            }
        return {
            "artifacts": [
                {
                    "name": tier_resolver.RECEIPT_ARTIFACT_NAME,
                    "expired": False,
                    "archive_download_url": "https://api.github.com/artifact/1",
                }
            ]
        }

    result = tier_resolver._verified_pr_matrix_receipt(
        _push_event(),
        [_merged_pull_request()],
        token="secret",
        source_tree_sha=TREE_SHA,
        policy=load_ci_execution_policy(),
        fetch_json=fetch_json,
        fetch_archive=lambda *_args, **_kwargs: _receipt_archive(receipt),
    )

    assert result == receipt


def test_normal_pr_main_journey_has_one_matrix_and_five_suite_executions() -> None:
    journey = normal_pr_main_journey()

    assert journey == {
        "pull_request_full_suite_executions": 4,
        "main_full_suite_executions": 1,
        "total_full_suite_executions": 5,
        "full_matrix_executions": 1,
    }


def test_ci_policy_is_independent_of_canonical_test_classification_projection(
    tmp_path: Path,
) -> None:
    (tmp_path / "config").mkdir()
    shutil.copy2(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy2(
        ROOT / "config" / "installation_preflight_contract.json",
        tmp_path / "config" / "installation_preflight_contract.json",
    )

    policy = load_ci_execution_policy(tmp_path)

    assert policy["contract"] == "pr_matrix_main_reference_v1"
    assert not (tmp_path / "config" / "distribution_test_profiles.json").exists()


def test_checked_in_workflow_matches_policy_and_has_no_collect_only_replay() -> None:
    workflow = (ROOT / ".github" / "workflows" / "quality-gate.yml").read_text(
        encoding="utf-8"
    )
    result = validate_workflow_text(workflow)

    assert result["passed"] is True
    assert result["full_suite_command_occurrences"] == 2
    assert result["full_matrix_literal_occurrences"] == 1
    assert result["target_lifecycle_command_occurrences"] == {
        "target_prepare": 2,
        "target_init": 2,
        "target_doctor": 2,
    }
    assert result["job_bindings"] == {
        "classifier": True,
        "matrix": True,
        "receipt": True,
        "reference": True,
    }
    assert "--collect-only" not in workflow


def test_checked_in_operator_document_matches_derived_execution_counts() -> None:
    documentation = (ROOT / "docs" / "CI_GITHUB_ACTIONS.md").read_text(
        encoding="utf-8"
    )

    assert validate_documentation_text(documentation) == {
        "passed": True,
        "missing_tokens": [],
        "normal_pr_main_journey": {
            "pull_request_full_suite_executions": 4,
            "main_full_suite_executions": 1,
            "total_full_suite_executions": 5,
            "full_matrix_executions": 1,
        },
    }


def test_workflow_validator_rejects_reintroduced_collect_only_or_second_matrix() -> None:
    workflow = (ROOT / ".github" / "workflows" / "quality-gate.yml").read_text(
        encoding="utf-8"
    )
    second_matrix = workflow.replace(
        'python-version: "3.12"',
        'python-version: ["3.11", "3.12", "3.13", "3.14"]',
        1,
    )

    assert validate_workflow_text(workflow + "\n# --collect-only\n")["passed"] is False
    assert validate_workflow_text(second_matrix)["passed"] is False


def test_workflow_validator_rejects_tier_name_detached_from_matrix_job() -> None:
    workflow = (ROOT / ".github" / "workflows" / "quality-gate.yml").read_text(
        encoding="utf-8"
    )
    detached = workflow.replace(
        "needs.ci-tier.outputs.tier == 'direct_main_push_full_matrix'",
        "needs.ci-tier.outputs.tier == 'pull_request_full_matrix'",
        1,
    )
    detached += "\n# direct_main_push_full_matrix\n"

    result = validate_workflow_text(detached)

    assert result["passed"] is False
    assert result["job_bindings"]["matrix"] is False
