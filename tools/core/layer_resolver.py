import os
from pathlib import Path
from tools.core.config import DOCTRINE

def resolve_layer(rel_path: str) -> str:
    """
    Identifies the hexagonal layer of a file based on its relative path 
    and the definitions in architecture_doctrine.json.
    """
    norm_path = rel_path.replace("\\", "/").lower()
    
    # Priority 1: Layer Rules from Doctrine
    layer_rules = DOCTRINE.get("layer_rules", [])
    for rule in layer_rules:
        layer_name = rule.get("layer")
        conditions = rule.get("conditions", {})
        path_fragments = conditions.get("path_fragments", [])
        
        if any(frag.lower() in norm_path for frag in path_fragments):
            return layer_name
            
    # Priority 2: Doctrine-driven Generic Path Hints
    hints = DOCTRINE.get("architectural_integrity_rules", {}).get("layer_identification_hints", [])
    for hint in hints:
        if hint.get("fragment", "").lower() in norm_path:
            return hint.get("layer")
    
    return "unknown"


def is_violation(source_layer: str, target_layer: str, language: str = "typescript") -> bool:
    """
    Checks if an import from source_layer to target_layer violates 
    architectural principles. Supports global and language-specific scoping.
    """
    integrity_rules = DOCTRINE.get("architectural_integrity_rules", {})
    
    # 1. Global Rules Check
    global_rules = integrity_rules.get("global_rules", [])
    for rule in global_rules:
        if rule.get("source") == source_layer:
            if target_layer in rule.get("targets", []):
                scope = rule.get("scope", [])
                if not scope or language in scope:
                    return True

    # 2. Language-Specific Rules Check
    lang_rules = integrity_rules.get("language_specific_rules", {}).get(language, [])
    for rule in lang_rules:
        if rule.get("source") == source_layer:
            if target_layer in rule.get("targets", []):
                return True

    # 3. Legacy Forbidden Mappings (Fallback)
    forbidden = integrity_rules.get("forbidden_mappings", [])
    for rule in forbidden:
        if rule.get("source") == source_layer:
            if target_layer in rule.get("targets", []):
                return True

    return False
