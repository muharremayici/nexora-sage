"""
Semantic Clone Detector
Groups symbols across the genome by their structural hash to detect exact 
or near-exact code clones (copy-pasted code) within or between projects.
"""
import json
from collections import defaultdict
from tools.core.config import RAW_DIR, REPORTS_DIR, DOCTRINE, save_json_atomic, save_text_atomic
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.atlas_io import load_atlas_data
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file
from tools.core.logger import logger

def run_clone_detector():
    logger.info("Running Semantic Clone Detector...")

    genome = load_genome_data()
    if not genome:
        logger.error("[FAIL] Genome payload not found. Cannot detect clones.")
        return False

    # Group by hash
    hash_groups = defaultdict(list)
    for symbol_name, blocks in genome.items():
        for block in blocks:
            # We don't have exact line count easily, but we have source_lines like 'L22-L30'
            src = block.get("source_lines", "")
            lines_count = 0
            if src and "-" in src:
                try:
                    p1, p2 = src.replace("L", "").split("-")
                    lines_count = abs(int(p2) - int(p1)) + 1
                except (ValueError, IndexError): pass
            
            # Ignore very small blocks to prevent false positives (e.g. 2 line getters)
            from tools.core.doctrine_contract import require_doctrine_path
            min_lines = require_doctrine_path("clone_detection", "min_lines", expected_type=(int, float))
            if lines_count < min_lines:
                continue
                
            h = block.get("dna")  # AST hash is stored as dna
            if h:
                derived_block = dict(block)
                derived_block["_lines_count"] = lines_count
                derived_block["_symbol_id"] = f"{block.get('project')}::{block.get('file')}::{block.get('name')}"
                hash_groups[h].append(derived_block)

    # Filter clones
    clones = [group for group in hash_groups.values() if len(group) > 1]
    
    # Sort by size (lines) descending
    clones.sort(key=lambda g: g[0].get("_lines_count", 0), reverse=True)

    # Calculate metrics
    total_cloned_blocks = sum(len(c) for c in clones)
    max_clone_size = clones[0][0].get("_lines_count", 0) if clones else 0
    wasted_lines = sum((len(c) - 1) * c[0].get("_lines_count", 0) for c in clones)

    results = {
        "metrics": {
            "clone_clusters": len(clones),
            "cloned_blocks": total_cloned_blocks,
            "max_clone_lines": max_clone_size,
            "wasted_lines_due_to_clones": wasted_lines
        },
        "clusters": []
    }

    for idx, c in enumerate(clones):
        results["clusters"].append({
            "cluster_id": idx + 1,
            "lines": c[0].get("_lines_count"),
            "hash": c[0].get("dna"),
            "instances": [
                {"id": x.get("_symbol_id"), "type": x.get("type"), "source": x.get("source_lines")}
                for x in c
            ]
        })

    raw_path = RAW_DIR / "clone_detector.json"
    save_json_atomic(raw_path, results)
    write_current_atlas_lineage(
        artifact_id="clone_detector",
        producer="tools.engines.clone_detector",
        artifact_payload=results,
        atlas=load_atlas_data(),
        dependency_payloads={"genome": genome},
    )

    # Markdown Report
    md_lines = [
        "# Semantic Code Clone Report",
        "",
        "> Detects identical code blocks (functions, classes) across the codebase based on AST structure. Ignores smaller blocks.",
        "",
        f"**Clone Clusters Found:** {len(clones)}",
        f"**Total Cloned Instances:** {total_cloned_blocks}",
        f"**Lines Wasted (Redundant):** {wasted_lines} lines",
        ""
    ]

    for cluster in results["clusters"][:50]:  # Limit top 50 in report
        md_lines.append(f"### Cluster #{cluster['cluster_id']} ({cluster['lines']} lines)")
        for inst in cluster["instances"]:
            md_lines.append(f"- `{inst.get('id')}`  *(type: {inst.get('type')}, lines: {inst.get('source')})*")
        md_lines.append("")

    save_text_atomic(REPORTS_DIR / "clone_detector.md", "\n".join(md_lines))
    logger.info(f"Clone Detector Finished: {len(clones)} clusters found.")
    return True

if __name__ == "__main__":
    run_clone_detector()
