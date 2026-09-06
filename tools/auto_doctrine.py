import os
import sys
import json

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.core.config import CONFIG_DIR, DISCOVERY_FILE, DOCTRINE_FILE, save_json_atomic
from tools.core.architecture_blueprints import canonical_profile_id

TEMPLATE_REGISTRY_FILE = CONFIG_DIR / "auto_doctrine_templates.json"

def load_json(path):
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)

def _load_templates():
    registry = load_json(TEMPLATE_REGISTRY_FILE)
    templates = registry.get("templates", {}) if isinstance(registry, dict) else {}
    return {str(key): value for key, value in templates.items() if isinstance(value, dict)}

def _discovery_paths(discovery):
    variations = discovery.get("variations", {}) if isinstance(discovery, dict) else {}
    return [str(path).replace("\\", "/").lower() for path in variations.values() if isinstance(path, str)]


def _infer_profile(discovery, templates):
    projects = discovery.get("_discovery_metadata", {}).get("projects", {}) if isinstance(discovery, dict) else {}
    main_proj = projects.get("MAIN", {}) if isinstance(projects, dict) else {}
    detected = str(main_proj.get("metadata", {}).get("detected_profile", "") or "").strip()
    canonical_detected = canonical_profile_id(detected)
    if canonical_detected in templates:
        return canonical_detected

    paths = _discovery_paths(discovery)
    joined = " ".join(paths)
    plugins = {str(item).lower() for item in (discovery.get("plugins", []) if isinstance(discovery, dict) else [])}
    if any(part in joined for part in ("apps/", "packages/")) or {"turbo", "pnpm-workspace", "workspace"} & plugins:
        return "MONOREPO_TURBOREPO"
    if "app/" in joined and ("next" in plugins or "next.config" in joined):
        return "NEXTJS_APP_ROUTER"
    if all(marker in joined for marker in ("features", "entities", "shared")):
        return "FSD_STRICT"
    if all(marker in joined for marker in ("domain", "application")) and any(marker in joined for marker in ("infra", "adapters")):
        return "CLEAN_ARCHITECTURE"
    if len(paths) <= 1 and not joined:
        return "MINIMAL"
    return "MODULAR_FLAT"

def main():
    if DOCTRINE_FILE.exists():
        print(f"[AUTO-DOCTRINE] Existing doctrine found at {DOCTRINE_FILE.name}. Skipping auto-generation (overrides preserved).")
        return 0

    discovery = load_json(DISCOVERY_FILE)
    templates = _load_templates()
    profile = _infer_profile(discovery, templates)
    print(f"[AUTO-DOCTRINE] Discovered architectural profile: {profile}")
    
    template = templates.get(profile)
    if not template:
        print("[AUTO-DOCTRINE] No exact template match. Falling back to default MODULAR_FLAT template.")
        template = templates.get("MODULAR_FLAT", {})
    if not template:
        print(f"[AUTO-DOCTRINE] Missing template registry at {TEMPLATE_REGISTRY_FILE.name}. Cannot bootstrap doctrine.")
        return 1
        
    save_json_atomic(DOCTRINE_FILE, template)
    print(f"[AUTO-DOCTRINE] Successfully auto-generated {DOCTRINE_FILE.name} for profile {profile}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
