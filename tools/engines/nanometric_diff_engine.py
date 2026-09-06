"""
AST / Genome Diff Engine - performs a structural diff between projects
by comparing the exact exported components based on their AST genome hash.
"""

import json
from collections import defaultdict

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.genome_io import load_genome_data
from tools.core.logger import logger
from tools.core.workspace_mode import get_workspace_mode


def run_nanometric_diff():
    logger.info("Running Nanometric AST/Genome Diff...")

    genome = load_genome_data()
    if not genome:
        logger.error("[FAIL] Genome payload not found. Cannot run AST diff.")
        return False

    projects_data = defaultdict(lambda: defaultdict(dict))

    for symbol_name, blocks in genome.items():
        for block in blocks:
            project_key = block.get("project")
            rel_file = block.get("file")
            if not project_key or not rel_file:
                continue
            projects_data[project_key][rel_file][symbol_name] = {
                "hash": block.get("dna"),
                "type": block.get("type"),
                "lines": block.get("source_lines"),
                "source": block.get("source_lines"),
            }

    workspace_mode = get_workspace_mode()
    project_keys = list(projects_data.keys())
    if len(project_keys) < 2 or not workspace_mode.get("comparative_enabled"):
        payload = {
            "status": "not_applicable",
            "reason": "comparative_diff_requires_multiple_projects",
            "workspace_mode": workspace_mode,
            "projects": project_keys,
        }
        json_path = RAW_DIR / "nanometric_diff.json"
        save_json_atomic(json_path, payload)

        md_lines = [
            "# [DNA] Nanometric Structural Diff",
            "",
            "Comparative AST/Genome diff is not applicable in this workspace.",
            "",
            f"- Workspace mode: `{workspace_mode.get('mode')}`",
            f"- Project count: `{workspace_mode.get('project_count')}`",
            f"- Variant projects: `{', '.join(workspace_mode.get('variant_projects') or []) or '-'}`",
            f"- Projects: `{', '.join(project_keys) if project_keys else '-'}`",
            "",
            "> A comparative diff only runs when the workspace contains at least one project with role `variant`.",
        ]
        md_path = REPORTS_DIR / "nanometric_diff.md"
        save_text_atomic(md_path, "\n".join(md_lines))
        logger.info(
            "[INFO] Nanometric diff not applicable in non-comparative workspace; "
            "wrote no-op diff artifacts."
        )
        return True
    from tools.core.config import DYNAMIC_CONFIG
    roles = DYNAMIC_CONFIG.get("project_roles", {})
    base_proj = next((k for k, v in roles.items() if v == "host" and k in project_keys), None)
    if not base_proj:
        base_proj = project_keys[0]
    target_projects = [project for project in project_keys if project != base_proj]

    reports = []
    diff_results = {}

    for target_proj in target_projects:
        diff_results[target_proj] = _compare_projects(base_proj, projects_data[base_proj], target_proj, projects_data[target_proj])
        report_md = _generate_markdown_report(base_proj, target_proj, diff_results[target_proj])
        md_path = REPORTS_DIR / f"nanometric_diff_{base_proj}_vs_{target_proj}.md"
        save_text_atomic(md_path, "\n".join(report_md))
        reports.append(md_path)

    json_path = RAW_DIR / "nanometric_diff.json"
    save_json_atomic(json_path, diff_results)
    index_path = REPORTS_DIR / "nanometric_diff.md"
    save_text_atomic(
        index_path,
        "\n".join(_generate_index_markdown(base_proj, target_projects, diff_results)),
    )

    logger.info(f"[OK] Nanometric Diffs generated for {len(reports)} comparisons.")
    return True


def _compare_projects(base_name, base_data, target_name, target_data):
    base_files = set(base_data.keys())
    target_files = set(target_data.keys())
    all_files = base_files.union(target_files)

    result = {"base": base_name, "target": target_name, "files_diff": []}

    for file_path in sorted(all_files):
        if file_path not in base_files:
            symbols = list(target_data[file_path].keys())
            result["files_diff"].append(
                {
                    "file": file_path,
                    "scoped_file": f"{target_name}::{file_path}",
                    "scoped_base_file": f"{base_name}::{file_path}",
                    "scoped_target_file": f"{target_name}::{file_path}",
                    "status": "ADDED_FILE",
                    "symbols_added": symbols,
                    "symbols_removed": [],
                    "symbols_modified": [],
                }
            )
        elif file_path not in target_files:
            symbols = list(base_data[file_path].keys())
            result["files_diff"].append(
                {
                    "file": file_path,
                    "scoped_file": f"{base_name}::{file_path}",
                    "scoped_base_file": f"{base_name}::{file_path}",
                    "scoped_target_file": f"{target_name}::{file_path}",
                    "status": "DELETED_FILE",
                    "symbols_added": [],
                    "symbols_removed": symbols,
                    "symbols_modified": [],
                }
            )
        else:
            base_symbols = base_data[file_path]
            target_symbols = target_data[file_path]
            common = set(base_symbols.keys()).intersection(set(target_symbols.keys()))
            added = set(target_symbols.keys()) - set(base_symbols.keys())
            removed = set(base_symbols.keys()) - set(target_symbols.keys())

            modified = []
            for sym in common:
                if base_symbols[sym].get("hash") != target_symbols[sym].get("hash"):
                    modified.append(sym)

            if added or removed or modified:
                result["files_diff"].append(
                    {
                        "file": file_path,
                        "scoped_file": f"{target_name}::{file_path}",
                        "scoped_base_file": f"{base_name}::{file_path}",
                        "scoped_target_file": f"{target_name}::{file_path}",
                        "status": "MODIFIED_FILE",
                        "symbols_added": list(added),
                        "symbols_removed": list(removed),
                        "symbols_modified": modified,
                    }
                )

    return result


def _generate_markdown_report(base_name, target_name, diff_data):
    lines = [
        f"# [DNA] Nanometric Structural Diff: `{base_name}` vs `{target_name}`",
        "",
        "> This report shows exact AST-level differences between the two variations. It ignores whitespace and formatting changes.",
        "",
    ]

    added_files = [d for d in diff_data["files_diff"] if d["status"] == "ADDED_FILE"]
    deleted_files = [d for d in diff_data["files_diff"] if d["status"] == "DELETED_FILE"]
    modified_files = [d for d in diff_data["files_diff"] if d["status"] == "MODIFIED_FILE"]

    stats = (
        f"**Total Changes:** {len(added_files)} Files Added | "
        f"{len(deleted_files)} Files Removed | "
        f"{len(modified_files)} Files Surgically Modified"
    )
    lines.extend([stats, ""])

    if modified_files:
        lines.extend(["## Surgically Modified Files", ""])
        for diff in modified_files:
            lines.append(f"### `{diff['file']}`")
            for added in diff["symbols_added"]:
                lines.append(f"- [ADDED] `{added}`")
            for modified in diff["symbols_modified"]:
                lines.append(f"- [MODIFIED] `{modified}` (Internal AST changed)")
            for removed in diff["symbols_removed"]:
                lines.append(f"- [REMOVED] `{removed}`")
            lines.append("")

    if added_files:
        lines.extend(["## New Files (Added in Target)", ""])
        for diff in added_files:
            symbol_list = ", ".join(f"`{symbol}`" for symbol in diff["symbols_added"][:5])
            more = f" (+{len(diff['symbols_added']) - 5} more)" if len(diff["symbols_added"]) > 5 else ""
            lines.append(f"- `{diff['file']}` *({len(diff['symbols_added'])} symbols: {symbol_list}{more})*")
        lines.append("")

    if deleted_files:
        lines.extend(["## Removed Files (Deleted in Target)", ""])
        for diff in deleted_files:
            lines.append(f"- `{diff['file']}` *(-{len(diff['symbols_removed'])} symbols)*")

    return lines


def _generate_index_markdown(base_name, target_projects, diff_results):
    lines = [
        "# [DNA] Nanometric Structural Diff",
        "",
        f"- Base project: `{base_name}`",
        f"- Comparison count: `{len(target_projects)}`",
        "",
        "> This index summarizes the current comparative AST/Genome diff artifact. "
        "Open a bounded comparison report for file- and symbol-level evidence.",
        "",
        "## Comparisons",
        "",
    ]
    for target_name in target_projects:
        rows = diff_results.get(target_name, {}).get("files_diff", [])
        added = sum(row.get("status") == "ADDED_FILE" for row in rows)
        removed = sum(row.get("status") == "DELETED_FILE" for row in rows)
        modified = sum(row.get("status") == "MODIFIED_FILE" for row in rows)
        report_name = f"nanometric_diff_{base_name}_vs_{target_name}.md"
        lines.append(
            f"- [`{base_name}` vs `{target_name}`]({report_name}): "
            f"{added} added, {removed} removed, {modified} modified files"
        )
    return lines


if __name__ == "__main__":
    run_nanometric_diff()
