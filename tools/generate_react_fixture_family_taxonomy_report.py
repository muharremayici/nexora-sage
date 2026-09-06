from __future__ import annotations

import sys
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.roadmap_phase_registry import allowed_roadmap_releases, current_product_release, load_roadmap_phase_registry


CONFIG_PATH = CONFIG_DIR / "react_fixture_family_taxonomy.json"
RAW_OUTPUT_PATH = RAW_DIR / "react_fixture_family_taxonomy_report.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "react_fixture_family_taxonomy_report.md"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _family_status(family: dict[str, Any]) -> str:
    support = str(family.get("support_level") or "unsupported")
    linked = [item for item in family.get("linked_existing_fixtures", []) if item]
    covered = [item for item in family.get("covered_variants", []) if item]
    if support == "unsupported":
        return "UNSUPPORTED"
    if support == "roadmap":
        return "ROADMAP"
    if support == "proven" and linked and covered:
        return "PROVEN"
    if support in {"partial", "smoke"} and (linked or covered):
        return support.upper()
    return "NEEDS_EVIDENCE"


def build_report() -> dict[str, Any]:
    config = load_json_file(CONFIG_PATH, {})
    phase_registry = load_roadmap_phase_registry()
    active_release = current_product_release(phase_registry)
    roadmap_releases = sorted(allowed_roadmap_releases(phase_registry))
    fallback_roadmap_release = roadmap_releases[0] if roadmap_releases else active_release
    families = config.get("families") or []
    release_policy = config.get("release_policy") if isinstance(config.get("release_policy"), dict) else {}
    included_releases = {str(item) for item in release_policy.get("included_releases", [active_release])}
    rows: list[dict[str, Any]] = []
    by_status: Counter[str] = Counter()
    by_phase: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    release_required_total = 0
    release_required_proven = 0
    release_family_total = 0
    release_family_proven = 0
    missing_fixture_variants: dict[str, list[str]] = defaultdict(list)

    for family in families:
        status = _family_status(family)
        support = str(family.get("support_level") or "unsupported")
        release = str(family.get("target_release") or fallback_roadmap_release)
        category = str(family.get("category") or "uncategorized")
        required = bool(family.get("required_for_release"))
        if required:
            release_required_total += 1
            if status == "PROVEN":
                release_required_proven += 1
        if release in included_releases:
            release_family_total += 1
            if status == "PROVEN":
                release_family_proven += 1
        by_status[status] += 1
        by_phase[release] += 1
        by_category[category] += 1
        for variant in family.get("missing_variants", []) or []:
            missing_fixture_variants[str(variant)].append(str(family.get("id")))
        rows.append(
            {
                "id": family.get("id"),
                "label": family.get("label"),
                "category": category,
                "support_level": support,
                "target_release": release,
                "required_for_release": required,
                "taxonomy_status": status,
                "linked_existing_fixtures": family.get("linked_existing_fixtures", []),
                "covered_variants": family.get("covered_variants", []),
                "missing_variants": family.get("missing_variants", []),
            }
        )

    summary = {
        "families": len(rows),
        "status_counts": dict(sorted(by_status.items())),
        "target_release_counts": dict(sorted(by_phase.items())),
        "category_counts": dict(sorted(by_category.items())),
        "release_required_total": release_required_total,
        "release_required_proven": release_required_proven,
        "release_required_ratio": round(release_required_proven / max(release_required_total, 1), 4),
        "included_releases": sorted(included_releases),
        "release_family_total": release_family_total,
        "release_family_proven": release_family_proven,
        "release_family_ratio": round(release_family_proven / max(release_family_total, 1), 4),
        "families_with_linked_fixtures": len([row for row in rows if row["linked_existing_fixtures"]]),
        "families_without_linked_fixtures": len([row for row in rows if not row["linked_existing_fixtures"]]),
        "top_missing_variants": {
            key: len(value)
            for key, value in sorted(missing_fixture_variants.items(), key=lambda item: (-len(item[1]), item[0]))
        },
        "status": "PASS" if (
            release_required_total
            and release_required_proven == release_required_total
            and release_family_total
            and release_family_proven == release_family_total
        ) else "ATTENTION",
    }
    return {
        "meta": {
            "kind": "react_fixture_family_taxonomy_report",
            "version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "tools.generate_react_fixture_family_taxonomy_report",
            "config": CONFIG_PATH.relative_to(ROOT).as_posix(),
            "config_sha256": _sha256_file(CONFIG_PATH),
        },
        "summary": summary,
        "families": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    rows = payload.get("families") or []
    lines = [
        "# React Fixture Family Taxonomy Report",
        "",
        "This report maps existing fixture/corpus evidence to the broader React fixture family universe.",
        "",
        "## Summary",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Families: `{summary.get('families')}`",
        f"- 1.0.0 required: `{summary.get('release_required_proven')}/{summary.get('release_required_total')}`",
        f"- 1.0.0 required ratio: `{summary.get('release_required_ratio')}`",
        f"- Included releases: `{summary.get('included_releases')}`",
        f"- Release families: `{summary.get('release_family_proven')}/{summary.get('release_family_total')}`",
        f"- Release family ratio: `{summary.get('release_family_ratio')}`",
        f"- Families with linked fixtures/evidence anchors: `{summary.get('families_with_linked_fixtures')}`",
        f"- Families without linked fixtures/evidence anchors: `{summary.get('families_without_linked_fixtures')}`",
        "",
        "## Status Counts",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    for status, count in (summary.get("status_counts") or {}).items():
        lines.append(f"| `{status}` | {count} |")
    lines.extend(["", "## Target Releases", "", "| Release | Count |", "|---|---:|"])
    for release, count in (summary.get("target_release_counts") or {}).items():
        lines.append(f"| `{release}` | {count} |")
    lines.extend(["", "## Families", "", "| Family | Category | Release | Support | Status | Covered variants | Missing variants |", "|---|---|---|---|---|---|---|"])
    for row in rows:
        covered = ", ".join(f"`{item}`" for item in row.get("covered_variants") or []) or "-"
        missing = ", ".join(f"`{item}`" for item in row.get("missing_variants") or []) or "-"
        lines.append(
            f"| `{row.get('id')}` | `{row.get('category')}` | `{row.get('target_release')}` | `{row.get('support_level')}` | `{row.get('taxonomy_status')}` | {covered} | {missing} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This taxonomy is a planning and evidence map, not a promise that all 80 families are equally deep in 1.0.0.",
            "- `PROVEN` means the family has existing fixture/corpus evidence anchors.",
            "- `PARTIAL`, `SMOKE`, `ROADMAP` and `UNSUPPORTED` are intentionally visible so the public claim does not overreach.",
            "- Missing variants guide future fixture promotion; they do not block the scoped 1.0.0 release unless the family is marked release-required.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_report()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_markdown(payload))
    summary = payload["summary"]
    print(
        f"[react-fixture-family-taxonomy] status={summary['status']} "
        f"families={summary['families']} required={summary['release_required_proven']}/{summary['release_required_total']}"
        f" release={summary['release_family_proven']}/{summary['release_family_total']}"
    )
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
