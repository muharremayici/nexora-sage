from __future__ import annotations

import json
from pathlib import Path

from tools.core.config import DYNAMIC_CONFIG, DOCTRINE


def load_host_policy():
    """Loads host merge policy from architecture doctrine."""
    policy = DOCTRINE.get("host_merge_policy", {})
    # Override logic for project-specific needs if DYNAMIC_CONFIG has overrides
    overrides = DYNAMIC_CONFIG.get("host_intelligence", {})
    if overrides:
        # Create a copy to avoid mutating global doctrine
        policy = dict(policy)
        policy.update(overrides)
    return policy


def glob_match(rel_path: str, patterns):
    path = Path(rel_path)
    return any(path.match(pattern) for pattern in (patterns or []))


def classify_host_policy(rel_path: str, policy):
    if glob_match(rel_path, policy.get("locked_patterns", [])):
        return "host_locked"
    if glob_match(rel_path, policy.get("manual_review_patterns", [])):
        return "manual_only"
    if glob_match(rel_path, policy.get("compose_preferred_patterns", [])):
        return "compose_preferred"
    return "replace_allowed"


def tag_host_file(rel_path: str, policy):
    tags = []
    if glob_match(rel_path, policy.get("public_boundary_patterns", [])):
        tags.append("public_boundary")
    if glob_match(rel_path, policy.get("integration_seam_patterns", [])):
        tags.append("integration_seam")
    
    # Use doctrine-driven tagging hints
    hints = policy.get("tagging_hints", [])
    for hint in hints:
        if hint.get("part") in rel_path:
            tags.append(hint.get("tag"))
            
    return tags


def load_json_file(path: Path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
