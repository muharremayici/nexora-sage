"""Standalone existing-log ingestion; not a target test/build runner."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_registry import artifact_paths
from tools.core.config import save_json_atomic
from tools.core.native_run_log_ingestion import _contract, ingest_native_run_logs
from tools.core.analysis_snapshot_lineage import load_atlas_commit, receipt_path, write_lineage_receipt
from tools.core.json_io import load_raw_artifact_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hash existing native logs; never execute target code.")
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--manifest", required=True, help="Relative manifest path inside the log bundle.")
    parser.add_argument("--trust-class")
    parser.add_argument("--diagnostic-format", choices=sorted(_contract()["normalization"]["formats"]),
                        help="Opt-in bounded warning observations; absence never means clean diagnostics.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--atlas-raw-dir", type=Path, help="Opt-in existing Atlas storage root; never refreshes Atlas.")
    parser.add_argument("--atlas-project", help="Exact Atlas project key; source correspondence only, not run proof.")
    args = parser.parse_args(argv)
    if (args.atlas_raw_dir is None) != (args.atlas_project is None):
        parser.error("Atlas storage root and project must be supplied together.")
    if args.atlas_raw_dir is not None and (args.atlas_raw_dir.name != ".raw" or not args.atlas_project):
        parser.error("Atlas storage root must be an existing .raw scope with a nonempty project key.")
    output = args.output or artifact_paths()["native_run_log_receipt"]
    if args.atlas_raw_dir is not None:
        raw_root = args.atlas_raw_dir.resolve()
        if output.resolve() == raw_root or raw_root in output.resolve().parents:
            parser.error("Native receipt output must be outside the Atlas storage root.")
    outputs = [output]
    if args.atlas_raw_dir is not None:
        outputs.append(receipt_path(args.atlas_raw_dir, _contract()["artifact_id"]))
    # Never overwrite a supplied log/manifest or any analyzed target input.
    for root in (args.target_root.resolve(), args.bundle_root.resolve()):
        if any(path.resolve() == root or root in path.resolve().parents for path in outputs):
            parser.error("Receipt output must be outside target and input bundle roots.")
    atlas = load_raw_artifact_path(args.atlas_raw_dir / "atlas.json", {}) if args.atlas_raw_dir is not None else None
    commit = load_atlas_commit(args.atlas_raw_dir) if args.atlas_raw_dir is not None else None
    payload = ingest_native_run_logs(target_root=args.target_root, bundle_root=args.bundle_root,
                                     manifest_path=args.manifest, trust_class=args.trust_class,
                                     diagnostic_format=args.diagnostic_format, atlas=atlas,
                                     atlas_commit=commit, atlas_project=args.atlas_project)
    save_json_atomic(output, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    if "diagnostic_observations" in payload:
        observed = payload["diagnostic_observations"]
        print(json.dumps({"diagnostic_status": observed["status"], "coverage": observed["coverage"],
                          "unique_observations": len(observed["items"]),
                          "occurrences": sum(len(row["occurrences"]) for row in observed["items"])}))
    lineage_ok = True
    if args.atlas_raw_dir is not None:
        lineage = write_lineage_receipt(artifact_id="native_run_log_receipt",
            producer="tools.ingest_native_run_logs", artifact_payload=payload,
            atlas=atlas if isinstance(atlas, dict) else {}, atlas_commit=commit or {}, raw_dir=args.atlas_raw_dir)
        lineage_ok = lineage["status"] == "COMPLETE"
        print(json.dumps({"atlas_source_status": payload["atlas_source_correspondence"]["status"],
                          "lineage_status": lineage["status"], "scope": "analysis_time_declared_source_text_only"}))
    return 0 if payload["summary"]["status"] == "INGESTED" and lineage_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
