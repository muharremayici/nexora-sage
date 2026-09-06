from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, OUTPUT_DIR, RAW_DIR, REPORTS_DIR, SCRIPTS_DIR, save_json_atomic, save_text_atomic


TRACKED_DIRS = {
    "raw": RAW_DIR,
    "reports": REPORTS_DIR,
    "scripts": SCRIPTS_DIR,
}
SELF_OUTPUT_PATHS = {
    RAW_DIR / "artifact_provenance_index.json",
    REPORTS_DIR / "artifact_provenance_index.md",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _config_hash() -> str:
    payload = []
    for path in sorted(CONFIG_DIR.rglob("*.json")):
        try:
            rel = path.relative_to(CODE_MAPS_DIR).as_posix()
        except ValueError:
            rel = path.as_posix()
        payload.append({"path": rel, "sha256": _sha256_file(path)})
    return _sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _artifact_rows() -> list[dict]:
    rows: list[dict] = []
    for family, directory in TRACKED_DIRS.items():
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            if path in SELF_OUTPUT_PATHS:
                continue
            stat = path.stat()
            rows.append(
                {
                    "family": family,
                    "path": path.relative_to(CODE_MAPS_DIR).as_posix(),
                    "bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    "sha256": _sha256_file(path),
                }
            )
    return rows


def build_artifact_provenance_index() -> dict:
    rows = _artifact_rows()
    generated_at = datetime.now(timezone.utc).isoformat()
    by_family: dict[str, dict] = {}
    for row in rows:
        family = row["family"]
        stats = by_family.setdefault(family, {"count": 0, "bytes": 0})
        stats["count"] += 1
        stats["bytes"] += int(row["bytes"])
    return {
        "meta": {
            "kind": "artifact_provenance_index",
            "version": "v1",
            "generated_at": generated_at,
            "generator": "tools.generate_artifact_provenance_index",
            "workspace_root": str(CODE_MAPS_DIR),
            "config_hash": _config_hash(),
        },
        "summary": {
            "artifact_count": len(rows),
            "families": by_family,
            "self_excluded_artifacts": sorted(path.relative_to(CODE_MAPS_DIR).as_posix() for path in SELF_OUTPUT_PATHS),
            "self_exclusion_reason": "artifact_provenance_index cannot truthfully hash its own final output without recursive self-reference",
        },
        "artifacts": rows,
    }


def render_report(index: dict) -> str:
    meta = index.get("meta", {})
    summary = index.get("summary", {})
    lines = [
        "# Artifact Provenance Index",
        "",
        f"- generated_at: `{meta.get('generated_at')}`",
        f"- generator: `{meta.get('generator')}`",
        f"- config_hash: `{meta.get('config_hash')}`",
        f"- artifact_count: `{summary.get('artifact_count')}`",
        f"- self_excluded_artifacts: `{summary.get('self_excluded_artifacts')}`",
        f"- self_exclusion_reason: `{summary.get('self_exclusion_reason')}`",
        "",
        "## Families",
        "",
        "| Family | Count | Bytes |",
        "|---|---:|---:|",
    ]
    for family, stats in sorted((summary.get("families") or {}).items()):
        lines.append(f"| `{family}` | {stats.get('count', 0)} | {stats.get('bytes', 0)} |")
    lines.extend(["", "## Largest Artifacts", "", "| Path | Family | Bytes | SHA256 |", "|---|---|---:|---|"])
    artifacts = sorted(index.get("artifacts", []), key=lambda item: int(item.get("bytes", 0)), reverse=True)
    for row in artifacts[:25]:
        lines.append(
            f"| `{row.get('path')}` | `{row.get('family')}` | {row.get('bytes')} | `{str(row.get('sha256'))[:16]}...` |"
        )
    return "\n".join(lines) + "\n"


def run() -> bool:
    index = build_artifact_provenance_index()
    save_json_atomic(RAW_DIR / "artifact_provenance_index.json", index)
    save_text_atomic(REPORTS_DIR / "artifact_provenance_index.md", render_report(index))
    return True


def main() -> None:
    run()


if __name__ == "__main__":
    main()
