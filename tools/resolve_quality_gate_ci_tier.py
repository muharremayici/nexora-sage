from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.quality_gate_ci import (
    current_git_tree_sha,
    load_ci_execution_policy,
    resolve_event_tier,
    verify_pr_matrix_receipt,
)


RECEIPT_ARTIFACT_NAME = "nexora-sage-pr-matrix-receipt"
RECEIPT_FILE_NAME = "quality-gate-pr-matrix-receipt.json"


def _load_event(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub event payload must be a JSON object")
    return payload


def _github_json(url: str, *, token: str, timeout_seconds: float = 20.0) -> Any:
    if not token:
        raise ValueError("GITHUB_TOKEN is required to read CI evidence")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "nexora-sage-quality-gate",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to obtain GitHub CI evidence: {exc}") from exc
    return payload


def _associated_pull_requests(
    event_payload: dict[str, Any],
    *,
    token: str,
    timeout_seconds: float = 20.0,
) -> list[dict[str, Any]]:
    repository = event_payload.get("repository")
    full_name = repository.get("full_name") if isinstance(repository, dict) else None
    normalized_repository = str(full_name or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", normalized_repository):
        raise ValueError("Push event is missing a valid repository identity")
    sha = str(event_payload.get("after") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Push event is missing an exact 40-character source commit")
    encoded_repository = urllib.parse.quote(normalized_repository, safe="/")
    url = f"https://api.github.com/repos/{encoded_repository}/commits/{sha}/pulls?per_page=100"
    payload = _github_json(url, token=token, timeout_seconds=timeout_seconds)
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise ValueError("GitHub associated pull-request response is invalid")
    return payload


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _download_artifact_archive(
    url: str,
    *,
    token: str,
    timeout_seconds: float = 20.0,
) -> bytes:
    if not token:
        raise ValueError("GITHUB_TOKEN is required to download CI evidence")
    if not url.startswith("https://api.github.com/"):
        raise ValueError("Artifact archive URL is outside the GitHub API boundary")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "nexora-sage-quality-gate",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            archive = response.read(2_000_001)
    except urllib.error.HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise ValueError(f"Unable to download PR matrix receipt: HTTP {exc.code}") from exc
        location = str(exc.headers.get("Location") or "")
        if not location.startswith("https://"):
            raise ValueError("Artifact redirect is missing a secure URL") from exc
        unsigned_request = urllib.request.Request(
            location,
            headers={"User-Agent": "nexora-sage-quality-gate"},
        )
        try:
            with urllib.request.urlopen(unsigned_request, timeout=timeout_seconds) as response:
                archive = response.read(2_000_001)
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as download_exc:
            raise ValueError(f"Unable to download redirected PR matrix receipt: {download_exc}") from download_exc
    except (OSError, urllib.error.URLError) as exc:
        raise ValueError(f"Unable to download PR matrix receipt: {exc}") from exc
    if len(archive) > 2_000_000:
        raise ValueError("PR matrix receipt archive exceeds the bounded size")
    return archive


def _receipt_from_archive(archive: bytes) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            files = [item for item in bundle.infolist() if not item.is_dir()]
            matches = [item for item in files if Path(item.filename).name == RECEIPT_FILE_NAME]
            if len(files) > 5 or len(matches) != 1 or matches[0].file_size > 262_144:
                raise ValueError("PR matrix receipt archive has an invalid bounded shape")
            payload = json.loads(bundle.read(matches[0]).decode("utf-8"))
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ValueError(f"PR matrix receipt archive is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("PR matrix receipt payload must be an object")
    return payload


def _exact_merged_pull_requests(
    event_payload: dict[str, Any],
    associated_pulls: list[dict[str, Any]],
    *,
    target_branch: str,
) -> list[dict[str, Any]]:
    after = str(event_payload.get("after") or "").lower()
    return [
        row
        for row in associated_pulls
        if row.get("merged_at")
        and str(row.get("merge_commit_sha") or "").lower() == after
        and isinstance(row.get("base"), dict)
        and row["base"].get("ref") == target_branch
    ]


def _verified_pr_matrix_receipt(
    event_payload: dict[str, Any],
    associated_pulls: list[dict[str, Any]],
    *,
    token: str,
    source_tree_sha: str,
    policy: dict[str, Any],
    fetch_json: Callable[..., Any] = _github_json,
    fetch_archive: Callable[..., bytes] = _download_artifact_archive,
) -> dict[str, Any]:
    repository = event_payload.get("repository")
    repository_name = str(repository.get("full_name") or "") if isinstance(repository, dict) else ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository_name):
        raise ValueError("Push event is missing a valid repository identity")
    exact_pulls = _exact_merged_pull_requests(
        event_payload,
        associated_pulls,
        target_branch=str(policy["target_branch"]),
    )
    if not exact_pulls:
        raise ValueError("No exact merged pull request is associated with the main commit")

    encoded_repository = urllib.parse.quote(repository_name, safe="/")
    workflow_name = urllib.parse.quote(Path(str(policy["workflow_path"])).name, safe="")
    candidate_errors: list[str] = []
    for pull in exact_pulls:
        number = pull.get("number")
        head = pull.get("head")
        head_sha = str(head.get("sha") or "").lower() if isinstance(head, dict) else ""
        if not isinstance(number, int) or number < 1 or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            candidate_errors.append("associated_pr_identity_invalid")
            continue
        runs_url = (
            f"https://api.github.com/repos/{encoded_repository}/actions/workflows/{workflow_name}/runs"
            f"?event=pull_request&head_sha={head_sha}&status=completed&per_page=20"
        )
        runs_payload = fetch_json(runs_url, token=token)
        runs = runs_payload.get("workflow_runs") if isinstance(runs_payload, dict) else None
        if not isinstance(runs, list):
            raise ValueError("Workflow-run evidence response is invalid")
        successful_runs = sorted(
            (
                run
                for run in runs
                if isinstance(run, dict)
                and run.get("event") == "pull_request"
                and run.get("conclusion") == "success"
                and str(run.get("head_sha") or "").lower() == head_sha
                and isinstance(run.get("id"), int)
            ),
            key=lambda run: (int(run.get("run_attempt") or 0), int(run["id"])),
            reverse=True,
        )
        for run in successful_runs:
            run_id = int(run["id"])
            artifacts_url = (
                f"https://api.github.com/repos/{encoded_repository}/actions/runs/{run_id}/artifacts?per_page=100"
            )
            artifacts_payload = fetch_json(artifacts_url, token=token)
            artifacts = artifacts_payload.get("artifacts") if isinstance(artifacts_payload, dict) else None
            if not isinstance(artifacts, list):
                raise ValueError("Workflow artifact evidence response is invalid")
            candidates = [
                artifact
                for artifact in artifacts
                if isinstance(artifact, dict)
                and artifact.get("name") == RECEIPT_ARTIFACT_NAME
                and artifact.get("expired") is False
                and isinstance(artifact.get("archive_download_url"), str)
            ]
            for artifact in candidates:
                try:
                    receipt = _receipt_from_archive(
                        fetch_archive(str(artifact["archive_download_url"]), token=token)
                    )
                    verify_pr_matrix_receipt(
                        receipt,
                        pull_request_number=number,
                        pull_request_head_sha=head_sha,
                        source_tree_sha=source_tree_sha,
                        repository=repository_name,
                        workflow_run_id=run_id,
                        workflow_run_attempt=int(run.get("run_attempt") or 0),
                        policy=policy,
                    )
                except ValueError as exc:
                    candidate_errors.append(str(exc))
                    continue
                return receipt
    detail = candidate_errors[-1] if candidate_errors else "receipt_not_found"
    raise ValueError(f"No verified PR matrix receipt is available: {detail}")


def _write_github_output(path: Path, tier: str, reason: str) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"tier={tier}\n")
        handle.write(f"reason={reason}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve the fail-closed CI execution tier for one GitHub event."
    )
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH", ""))
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = parser.parse_args()

    try:
        if not args.event_path:
            raise ValueError("GitHub event path is required")
        event_payload = _load_event(Path(args.event_path))
        policy = load_ci_execution_policy()
        associated_pulls = None
        reason = "pull_request_full_matrix"
        if args.event_name == "push":
            fallback_tier = resolve_event_tier(
                args.event_name,
                event_payload,
                associated_pulls=[],
                policy=policy,
            )
            try:
                associated_pulls = _associated_pull_requests(
                    event_payload,
                    token=os.environ.get("GITHUB_TOKEN", ""),
                )
            except ValueError as exc:
                tier = fallback_tier
                reason = "association_evidence_unavailable_full_matrix_fallback"
                print(f"[quality-gate-tier] ATTENTION: {exc}; using full matrix", file=sys.stderr)
            else:
                try:
                    _verified_pr_matrix_receipt(
                        event_payload,
                        associated_pulls,
                        token=os.environ.get("GITHUB_TOKEN", ""),
                        source_tree_sha=current_git_tree_sha(),
                        policy=policy,
                    )
                except ValueError as exc:
                    tier = fallback_tier
                    reason = "pr_matrix_receipt_unavailable_full_matrix_fallback"
                    print(
                        f"[quality-gate-tier] ATTENTION: {exc}; using full matrix",
                        file=sys.stderr,
                    )
                else:
                    tier = resolve_event_tier(
                        args.event_name,
                        event_payload,
                        associated_pulls=associated_pulls,
                        matrix_receipt_verified=True,
                        policy=policy,
                    )
                    reason = "verified_content_bound_pr_matrix_reference"
        else:
            tier = resolve_event_tier(
                args.event_name,
                event_payload,
                associated_pulls=associated_pulls,
                policy=policy,
            )
        if not args.github_output:
            raise ValueError("GitHub output path is required")
        _write_github_output(Path(args.github_output), tier, reason)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[quality-gate-tier] FAIL: {exc}", file=sys.stderr)
        return 2

    print(f"[quality-gate-tier] PASS tier={tier} reason={reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
