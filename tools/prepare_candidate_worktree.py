"""Prepare a detached release-development checkout against a reviewed Atlas primary."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import ntpath
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.atlas_integrity import source_fingerprint
from tools.core.json_io import load_raw_artifact_primary_with_identity
from tools.core.source_layer_classifier import is_ignored


def _git(root, *args, input_bytes=None, index=None):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    return subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args],
                          check=True, stdout=subprocess.PIPE, input=input_bytes, env=env).stdout


def _reserved(name):
    if hasattr(ntpath, "isreserved"):
        return ntpath.isreserved(name)
    return PureWindowsPath(name).is_reserved()


def _safe_path(root, name):
    rel = PurePosixPath(name)
    if (not rel.parts or rel.is_absolute() or rel.as_posix() != name
            or any(p in (".", "..") or p.casefold() == ".git" or p.endswith((".", " "))
                   or _reserved(p) for p in rel.parts)
            or any(c in name for c in ("\\", ":", "\0"))):
        raise ValueError(f"unsafe reviewed path: {name!r}")
    path = root.joinpath(*rel.parts)
    if path.resolve() != path or not path.is_file():
        raise ValueError(f"reviewed regular file unavailable: {name}")
    return path


def _inventory(atlas):
    files = {}
    for project, metadata in atlas.items():
        if project == "symbols":
            continue
        for key, row in metadata["files"].items():
            name = row["repo_relative_path"]
            digest = row["hash"]
            if (row.get("canonical_file_path") is not True or row.get("project_key") != project
                    or row.get("atlas_rel_path") != key or not isinstance(name, str)
                    or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)
                    or files.setdefault(name, digest) != digest):
                raise ValueError(f"invalid reviewed Atlas file: {project}/{key}")
    if not files or len({os.path.normcase(p) for p in files}) != len(files):
        raise ValueError("empty or ambiguous reviewed source inventory")
    return files


def _read_inventory(install, expected):
    raw = install / "output/.raw"
    primary = load_raw_artifact_primary_with_identity(raw / "atlas.json")
    record = load_raw_artifact_primary_with_identity(raw / "atlas_commit.json")
    if not primary or primary[1] != "sqlite:" + expected or not record:
        raise ValueError("reviewed Atlas primary does not match the supplied digest")
    atlas, commit = primary[0], record[0]
    hashes = [commit[k] for k in ("atlas_sha256", "configuration_fingerprint",
                                   "generator_fingerprint", "source_fingerprint")]
    if (commit.get("state") != "complete" or hashes[0] != expected
            or any(not re.fullmatch("[a-f0-9]{64}", h) for h in hashes)
            or sha256(":".join(hashes).encode()).hexdigest() != commit["snapshot_id"]
            or source_fingerprint(atlas) != hashes[-1]):
        raise ValueError("reviewed Atlas commit or source inventory is unbound")
    return _inventory(atlas), commit["snapshot_id"]


def _target_tree(root, revision, install_name):
    entries = [row.split(b"\t", 1) for row in _git(root, "ls-tree", "-z", revision).split(b"\0") if row]
    own = os.fsencode(install_name)
    if not any(name == own and entry.startswith(b"040000 tree ") for entry, name in entries):
        raise ValueError("the reviewed installation must be a tracked directory at the repository root")
    return [(entry, name) for entry, name in entries if name != own]


def _unchanged_install(source, revision, head, install_name):
    def blobs(ref):
        entries = _git(source, "ls-tree", "-rz", ref, "--", install_name)
        rows = [entry.split(b"\t", 1) for entry in entries.split(b"\0") if entry]
        return {os.fsdecode(name): mode for mode, name in rows if mode.startswith((b"100644 blob ", b"100755 blob "))}
    original, candidate = blobs(head), blobs(revision)
    return {name: sha256(_safe_path(source, name).read_bytes()).hexdigest()
            for name, blob in original.items() if candidate.get(name) == blob
            and not is_ignored(source / name, root=source / install_name)}


def _checkout_sources(source, destination, revision, files):
    # The commit and target trees are already pinned. Never replace substantive edits or
    # override explicit checkout attributes. Reconcile legacy per-file EOLs only here,
    # in the new checkout, against bytes already approved in the reviewed primary.
    tracked = {}
    for record in _git(destination, "ls-files", "-z", "--eol").split(b"\0"):
        if record:
            eol, name = record.split(b"\t", 1)
            tracked[os.fsdecode(name)] = eol.decode("ascii").strip()
    for name in files:
        if name not in tracked:
            raise ValueError(f"reviewed source is not tracked: {name}")
        _safe_path(destination, name)

    # Resolve Git rules in one batch before reading or replacing any candidate bytes.
    data = _git(destination, "check-attr", "--stdin", "-z", "text", "eol", "filter",
                "working-tree-encoding", "ident",
                input_bytes=b"".join(os.fsencode(name) + b"\0" for name in files)).split(b"\0")
    attrs = {}
    for name, key, value in zip(data[0::3], data[1::3], data[2::3]):
        attrs.setdefault(os.fsdecode(name), {})[key] = value

    adjusted = set()
    for name, digest in files.items():
        path = _safe_path(destination, name)
        current = path.read_bytes()
        if sha256(current).hexdigest() == digest:
            continue
        approved = _safe_path(source, name).read_bytes()
        flags = tracked[name]
        attr = attrs[name]
        if (sha256(approved).hexdigest() != digest or b"\0" in approved or b"\0" in current
                or not re.search(r"\bw/(?:lf|crlf|mixed)\b", flags)
                or attr[b"text"] not in (b"unspecified", b"set", b"auto")
                or any(attr[k] != b"unspecified" for k in (b"eol", b"filter", b"working-tree-encoding", b"ident"))
                or approved.replace(b"\r\n", b"\n") != current.replace(b"\r\n", b"\n")):
            raise ValueError(f"candidate source differs from reviewed bytes: {name}")
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
            tmp = Path(out.name)
            try:
                out.write(approved)
            except BaseException:
                out.close()
                tmp.unlink(missing_ok=True)
                raise
        try:
            tmp.chmod(stat.S_IMODE(path.stat().st_mode))
            os.replace(tmp, path)
            adjusted.add(name)
        finally:
            tmp.unlink(missing_ok=True)
    for name, digest in files.items():
        if sha256(_safe_path(destination, name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"final candidate byte verification failed: {name}")
    if _git(destination, "rev-parse", "HEAD").decode().strip() != revision:
        raise ValueError("candidate revision changed during preparation")
    return adjusted


def _refresh_worktree_index(destination, revision, adjusted):
    """Refresh the new checkout's stat cache only if Git reproduces the exact commit tree."""
    if not adjusted:
        return
    index = Path(_git(destination, "rev-parse", "--path-format=absolute", "--git-path", "index").decode().strip())
    lock = index.with_name(index.name + ".lock")
    names = b"".join(os.fsencode(name) + b"\0" for name in sorted(adjusted))
    holder = lock.open("xb")
    try:
        with holder, tempfile.TemporaryDirectory(dir=index.parent) as temp:
            trial = Path(temp) / "index"
            shutil.copyfile(index, trial)
            _git(destination, "-c", "core.autocrlf=true", "update-index", "-z", "--stdin",
                 input_bytes=names, index=trial)
            _git(destination, "diff-index", "--cached", "--quiet", revision, "--", index=trial)
            with trial.open("rb") as ready:
                shutil.copyfileobj(ready, holder)
        os.replace(lock, index)
    except BaseException:
        lock.unlink(missing_ok=True)
        raise


def prepare(reviewed_install, revision, destination, expected):
    source = reviewed_install.parent
    if (not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision)
            or not re.fullmatch(r"[0-9a-f]{64}", expected)):
        raise ValueError("full candidate and Atlas identities required")
    if (not destination.is_absolute() or destination != destination.resolve()
            or destination.is_relative_to(source) or not destination.parent.is_dir()):
        raise ValueError("a new checkout outside the reviewed tree is required")
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    if (reviewed_install != reviewed_install.resolve(strict=True)
            or Path(_git(source, "rev-parse", "--show-toplevel").decode().strip()).resolve() != source):
        raise ValueError("reviewed installation does not belong to the specified repository root")
    git_admin = Path(_git(source, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve()
    if destination.is_relative_to(git_admin) or any(p.casefold() == ".git" for p in destination.parts):
        raise ValueError("destination is inside Git administration")
    head = _git(source, "rev-parse", "HEAD").decode().strip()
    if (_git(source, "rev-parse", "--verify", revision + "^{commit}").decode().strip() != revision
            or _target_tree(source, head, reviewed_install.name) != _target_tree(source, revision, reviewed_install.name)):
        raise ValueError("candidate changes the reviewed target tree")
    files, snapshot = _read_inventory(reviewed_install, expected)
    for name, digest in files.items():
        path = _safe_path(source, name)
        if path.is_relative_to(reviewed_install) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"source no longer matches the reviewed Atlas: {name}")
    install_files = _unchanged_install(source, revision, head, reviewed_install.name)
    destination.mkdir()  # Claim exclusively; a failed checkout remains available for inspection.
    _git(source, "-c", "core.autocrlf=false", "-c", "core.eol=lf", "worktree", "add",
         "--quiet", "--detach", "--", str(destination), revision)
    adjusted = _checkout_sources(source, destination, revision, {**files, **install_files})
    _refresh_worktree_index(destination, revision, adjusted)
    return {"status": "REVIEWED_SOURCE_BYTES_MATCH", "candidate_commit": revision, "reviewed_commit": head,
            "reviewed_atlas_sha256": expected, "reviewed_atlas_snapshot": snapshot,
            "verified_files": len(files), "restored_line_endings": len(adjusted.intersection(files)),
            "unchanged_install_files_verified": len(install_files),
            "install_line_endings_restored": len(adjusted.intersection(install_files)),
            "candidate_install": str(destination / reviewed_install.name), "publication_authority": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewed-install", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--expected-atlas-sha256", required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.reviewed_install, args.revision, args.destination, args.expected_atlas_sha256)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "CHECKOUT_NOT_VERIFIED", "reason": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
