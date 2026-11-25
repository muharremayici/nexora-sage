"""Portable identity of two manually reviewed default-target packets.

Keep the packet digest exact. This second identity is scoped to manual review
in worktrees of the same repository. It grants no execution or proof reuse.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess

from tools.core.json_io import load_raw_artifact_primary_with_identity, raw_artifact_primary_content_fingerprint


def _digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(data.encode("ascii")).hexdigest()


def _scope(install: Path, target: Path):
    if target != install.parent or target != target.resolve() or install != install.resolve():
        raise ValueError("unowned_review_target")
    # The common Git directory identifies this local repository across worktrees.
    # The sibling trees bind the committed target outside its SAGE installation.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    def git(*args):
        return subprocess.run(["git", "--no-optional-locks", "-C", str(target), *args],
                              check=True, capture_output=True, timeout=10, env=env).stdout
    root, common = git("rev-parse", "--path-format=absolute", "--show-toplevel",
                       "--git-common-dir").decode("utf-8").splitlines()
    if Path(root).resolve() != target:
        raise ValueError("unowned_review_target")
    entries = [row.split(b"\t", 1) for row in git("ls-tree", "-z", "HEAD").split(b"\0") if row]
    own = os.fsencode(install.name)
    if not any(name == own and entry.startswith(b"040000 tree ") for entry, name in entries):
        raise ValueError("review_installation_not_tracked")
    return _digest([os.path.normcase(str(Path(common).resolve(strict=True))), install.name,
                    [(entry.hex(), name.hex()) for entry, name in entries if name != own]])


def _object_line(text, label):
    matches = [line for line in text.splitlines() if line.lstrip().startswith(label)]
    if len(matches) != 1:
        raise ValueError("ambiguous_review_field")
    line = matches[0]
    prefix, raw = line.split(label, 1)
    data = json.loads(raw)
    if not isinstance(data, dict) or json.dumps(data, ensure_ascii=False) != raw:
        raise ValueError("noncanonical_review_field")
    return line, prefix + label, data


def _missing_watchdog(text, target, install):
    line, prefix, recovery = _object_line(text, "recovery: ")
    cli = recovery["cli_manual_alternative"]
    if (recovery["analysis_root"] != str(target) or cli["cwd"] != str(install)
            or recovery["target_root"] != "" or recovery["profile_id"] != ""
            or recovery["operation"] != "watchdog_pulse" or recovery["refresh_executed"] is not False
            or recovery["execution_authority"] != "not_granted" or cli["execution_authority"] != "not_granted"):
        raise ValueError("watchdog_review_scope_changed")
    recovery["analysis_root"] = "<owned-analysis-root>"
    cli["cwd"] = "<owned-cli-directory>"
    return text.replace(line, prefix + json.dumps(recovery, ensure_ascii=False))


def _state_flow(text, target, install, raw, observation):
    roots = [s.strip() for s in text.splitlines() if s.strip().startswith("analysis_root: ")]
    if not roots:
        raise ValueError("state_flow_review_target_unavailable")
    if roots != ["analysis_root: " + json.dumps(str(target), ensure_ascii=False)]:
        raise ValueError("state_flow_review_target_changed")
    root_line = roots[0]
    text = text.replace(root_line, 'analysis_root: "<owned-analysis-root>"', 1)
    text = re.sub(r"(?m)^  estimated_tokens: [0-9]+$", "  estimated_tokens: <display-cost>", text)
    line, prefix, binding = _object_line(text, "artifact_binding: ")
    commit_entry = load_raw_artifact_primary_with_identity(raw / "atlas_commit.json")
    flow_entry = load_raw_artifact_primary_with_identity(raw / "state_flow.json")
    if not commit_entry or not flow_entry:
        raise ValueError("state_flow_review_inputs_missing")
    commit, flow = commit_entry[0], flow_entry[0]
    components = [commit[k] for k in ("atlas_sha256", "configuration_fingerprint",
                                       "generator_fingerprint", "source_fingerprint")]
    if any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in components):
        raise ValueError("state_flow_review_identity_invalid")
    snapshot = sha256(":".join(components).encode("ascii")).hexdigest()
    meta = flow["run_meta"]
    if (commit["state"] != "complete" or snapshot != commit["snapshot_id"]
            or snapshot != binding["atlas_snapshot_id"] or snapshot != meta["atlas_snapshot_id"]
            or meta["analyzed_source_fingerprint"] != commit["source_fingerprint"]
            or meta["atlas_source_binding"] != "source_inventory_match"
            or binding["status"] != "stored_atlas_snapshot_match"):
        raise ValueError("state_flow_review_inputs_unbound")
    if raw_artifact_primary_content_fingerprint(raw / "atlas.json") != "sqlite:" + commit["atlas_sha256"]:
        raise ValueError("state_flow_atlas_primary_unbound")
    if observation is not None:
        observation["atlas_inputs"] = {
            key: commit[key] for key in ("source_fingerprint", "configuration_fingerprint", "generator_fingerprint")
        }
    binding["atlas_snapshot_id"] = "<bound-atlas-snapshot>"
    # Compare the complete state-flow result and execution scope. Only the
    # separately bound snapshot ID and elapsed profiling times may differ.
    flow_meta = {k: v for k, v in meta.items() if k not in ("atlas_snapshot_id", "profile_timings")}
    timings = meta.get("profile_timings", {})
    elapsed = {"atlas_load_seconds", "build_results_seconds", "genome_index_seconds", "provider_check_seconds"}
    if not isinstance(timings, dict):
        raise ValueError("state_flow_profiling_metadata_invalid")
    remaining = {k: v for k, v in timings.items() if k not in elapsed or type(v) not in (int, float) or v < 0}
    if remaining:
        flow_meta["profile_timings"] = remaining
    producer = install / "tools/engines/state_flow_scanner.py"
    return [text.replace(line, prefix + json.dumps(binding, ensure_ascii=False)),
            components[1:], sha256(producer.read_bytes()).hexdigest(),
            _digest({**flow, "run_meta": flow_meta})]


def review_identity(name, original, *, install, target, raw, observation=None):
    if name not in ("state_flow", "watchdog_session_missing"):
        return None
    try:
        scope = _scope(install, target)
        packet = (_missing_watchdog(original, target, install) if name == "watchdog_session_missing"
                  else _state_flow(original, target, install, raw, observation))
        return _digest(["sage_owned_worktree_manual_review_v1", name, scope, packet])
    except (KeyError, TypeError, ValueError, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        if observation is not None:
            reason = str(exc)
            observation["binding_error"] = (reason if isinstance(exc, ValueError)
                and re.fullmatch(r"(?:state_flow|watchdog|review|unowned|ambiguous|noncanonical)_[a-z_]+", reason)
                else "review_inputs_unavailable")
        return None
