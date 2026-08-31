import json
import re
import os
import concurrent.futures
from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter
from typing import Dict, List, Tuple

from tools.core.config import ROOT, MAIN_PROJECT_ROOT, OUTPUT_DIR, RAW_DIR, REPORTS_DIR, SOURCE_EXTENSIONS, SKIP_DIRS, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file
from tools.core.fractal_io import load_fractal_map_data, save_fractal_map_data
from tools.core.fractal_policy import (
    get_layer_rules,
    get_module_container,
    get_main_module_base_prefix,
    get_platform_default_path,
    get_platform_mapping,
    get_scoring_config,
)
from tools.core.config import DOCTRINE
from tools.core.doctrine_contract import require_dead_code_policy, require_doctrine_mapping, require_doctrine_path
from tools.core.projects_registry import (
    STUDIO_TO_MODULE,
    canonical_project_name,
    resolve_runtime_projects,
)
from tools.core.studio_resolver import (
    PLATFORM_CORE_STUDIO,
    domain_bucket,
    resolve_target_studio_context,
)
from tools.core.semantic_tokens import SEMANTIC_TOKENS, CAPABILITY_KEYWORDS
from tools.core.artifact_validator import ensure_valid_payload
from tools.core.logger import logger
from tools.core.source_snapshot_reader import load_source_text
from tools.core.workspace_mode import get_workspace_mode, is_source_allowed_for_host_merge
from tools.core.honesty_telemetry import record_honesty_event

from tools.core.db import get_current_tenant

class SizeBoundedDict(dict):
    def __init__(self, max_size=10, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_size = max_size
        self._keys_order = list(self.keys())

    def __setitem__(self, key, value):
        if key in self:
            self._keys_order.remove(key)
        self._keys_order.append(key)
        super().__setitem__(key, value)
        if len(self) > self.max_size:
            oldest = self._keys_order.pop(0)
            super().pop(oldest, None)

    def pop(self, key, default=None):
        if key in self._keys_order:
            self._keys_order.remove(key)
        return super().pop(key, default)

    def clear(self):
        self._keys_order.clear()
        super().clear()

class TenantIsolatedCache(dict):
    def __init__(self, max_size=10):
        self._tenants_map = SizeBoundedDict(max_size=max_size)

    def _get_current_dict(self):
        tenant_id = get_current_tenant()
        if tenant_id not in self._tenants_map:
            self._tenants_map[tenant_id] = {}
        return self._tenants_map[tenant_id]

    def __getitem__(self, key):
        return self._get_current_dict()[key]

    def __setitem__(self, key, value):
        self._get_current_dict()[key] = value

    def __delitem__(self, key):
        del self._get_current_dict()[key]

    def __contains__(self, key):
        return key in self._get_current_dict()

    def __iter__(self):
        return iter(self._get_current_dict())

    def get(self, key, default=None):
        return self._get_current_dict().get(key, default)

    def setdefault(self, key, default=None):
        return self._get_current_dict().setdefault(key, default)

    def update(self, *args, **kwargs):
        self._get_current_dict().update(*args, **kwargs)

    def clear(self):
        self._get_current_dict().clear()

    def pop(self, key, default=None):
        return self._get_current_dict().pop(key, default)

    def keys(self):
        return self._get_current_dict().keys()

    def values(self):
        return self._get_current_dict().values()

    def items(self):
        return self._get_current_dict().items()

    def __len__(self):
        return len(self._get_current_dict())

    def __bool__(self):
        return bool(self._get_current_dict())

    def __repr__(self):
        return f"TenantIsolatedCache(current_tenant={get_current_tenant()}, data={self._get_current_dict()!r})"

GLOBAL_FRACTAL_CACHE = TenantIsolatedCache(max_size=10)

ALL_SEMANTIC_TOKENS = [re.escape(token) for tokens in SEMANTIC_TOKENS.values() for token in tokens]
COMBINED_SEMANTIC_PATTERN = re.compile("|".join(ALL_SEMANTIC_TOKENS), re.IGNORECASE) if ALL_SEMANTIC_TOKENS else re.compile(r"$^")

LOW_SIGNAL_NAME_STOPLIST = set(require_doctrine_path("low_signal_tokens", expected_type=list))


def read_project_text(project_key: str, rel_path: str, path: Path) -> str:
    content = load_source_text(
        project_key,
        rel_path,
        fallback_path=path,
        component="fractal_mapper",
    )
    return content or ""


def normalize_project_name(name: str) -> str:
    return canonical_project_name(name)


def empty_symbol_buckets() -> Dict[str, List[str]]:
    return {
        "classes": [],
        "functions": [],
        "hooks": [],
        "interfaces": [],
        "types": [],
    }


def bucket_symbol_name(symbols: Dict[str, set], occ_type: str, name: str):
    if not name or name == "__file_meta__":
        return

    bucketing = require_doctrine_path("symbol_bucketing", expected_type=dict)
    normalized_type = str(occ_type or "")
    
    bucket = bucketing.get(normalized_type)
    
    if bucket:
        symbols[bucket].add(name)
    elif normalized_type in {"Hook", "Arrow/Hook"} or str(name).startswith("use"):
        # Fallback for dynamic hooks not explicitly in bucketing
        symbols["hooks"].add(name)
    else:
        symbols["functions"].add(name)


def build_file_genome_map(genome: Dict[str, List[Dict]], project_name: str) -> Dict[str, Dict]:
    file_genome_map = {}
    for occs in genome.values():
        for occ in occs:
            if occ.get("project") != project_name:
                continue
            file_rel = str(occ.get("file", ""))
            if file_rel not in file_genome_map:
                file_genome_map[file_rel] = {
                    "leaks": set(),
                    "features": set(),
                    "dna": occ.get("dna"),
                    "deps": occ.get("deps", {}),
                    "symbols": {
                        "classes": set(),
                        "functions": set(),
                        "hooks": set(),
                        "interfaces": set(),
                        "types": set(),
                    },
                }
            file_genome_map[file_rel]["leaks"].update(occ.get("leaks", []))
            file_genome_map[file_rel]["features"].update(occ.get("features", []))
            bucket_symbol_name(
                file_genome_map[file_rel]["symbols"],
                occ.get("type", ""),
                str(occ.get("name", "")),
            )

    for file_rel, data in file_genome_map.items():
        data["symbols"] = {
            key: sorted(values)
            for key, values in data["symbols"].items()
        }
    return file_genome_map


def map_project(
    project_root: Path,
    project_name: str,
    genome: Dict[str, List[Dict]],
    prev_data: Dict = None,
    changed_files: List[str] = None,
    atlas_files: Dict[str, Dict] | None = None,
) -> Dict:
    project_start = perf_counter()
    import hashlib
    import os
    use_md5_lift_fallback = os.getenv("CODEMAPS_FRACTAL_MD5_LIFT", "0").strip().lower() in {"1", "true", "yes", "on"}
    semantic_mode = os.getenv("CODEMAPS_FRACTAL_SEMANTIC_MODE", "atlas").strip().lower()
    use_atlas_semantic = semantic_mode != "content"
    semantic_from_atlas = 0
    semantic_from_content = 0

    files = []
    totals = Counter()
    depth = Counter()
    top_dirs = Counter()

    # Index previous files by path for fast lookup
    prev_files = {f["path"]: f for f in prev_data.get("files", [])} if prev_data else {}
    file_genome_map = build_file_genome_map(genome, project_name) if genome else {}

    # [Phase 4] Surgical Walk Avoidance
    all_target_rel_paths = set()
    if changed_files:
        all_target_rel_paths = {f.split("::", 1)[1] for f in changed_files if f.startswith(f"{project_name}::")}
        
    # Determine the set of files to process
    # If surgical, we take (All Prev Files) and update those in all_target_rel_paths
    if changed_files and prev_files:
        files_to_iter = []
        for rel in prev_files:
            if rel in all_target_rel_paths:
                files_to_iter.append(project_root / rel)
            else:
                # Direct Lift from cache without even checking stat!
                f_data = dict(prev_files[rel])
                files.append(f_data)
                totals["classes"] += len(f_data["symbols"]["classes"])
                totals["functions"] += len(f_data["symbols"]["functions"])
                totals["hooks"] += len(f_data["symbols"]["hooks"])
                totals["interfaces"] += len(f_data["symbols"]["interfaces"])
                totals["types"] += len(f_data["symbols"]["types"])
                depth[len(Path(rel).parts)] += 1
        
        # Also process NEW files that weren't in prev_files
        for rel in all_target_rel_paths:
            if rel not in prev_files:
                files_to_iter.append(project_root / rel)
    else:
        # The committed Atlas owns the downstream file universe.
        files_to_iter = [project_root / rel for rel in sorted(atlas_files or {})]

    loop_start = perf_counter()
    lifted_files = 0
    scanned_files = 0
    missing_files = []
    for f in files_to_iter:
        rel = f.relative_to(project_root).as_posix()

        genome_data = file_genome_map.get(rel, {})
        symbol_buckets = genome_data.get("symbols") or empty_symbol_buckets()
        cached = prev_files.get(rel)
        lifted = False

        if cached:
            # Fast-path: when Nuclear DNA is unchanged, lift without filesystem stat I/O.
            cached_dna = cached.get("dna")
            genome_dna = genome_data.get("dna")
            if genome_dna and cached_dna and genome_dna == cached_dna:
                lifted = True

        f_mtime = -1
        f_size = -1
        if cached and not lifted:
            # [Tier 2] Metadata/MD5 Check
            try:
                f_stat = os.stat(f)
                f_mtime = f_stat.st_mtime
                f_size = f_stat.st_size
            except Exception as exc:
                record_honesty_event(
                    component="fractal_mapper",
                    category="caught_error",
                    operation="file_stat_for_lift",
                    subject=str(f),
                    reason="file metadata unavailable during fractal lift check",
                    fallback="scan_without_metadata_lift",
                    claim_impact="performance_only",
                    exception=exc,
                )
                f_mtime = -1
                f_size = -1

        if cached and not lifted:
            prev_mtime = cached.get("mtime")
            prev_hash = cached.get("hash")
            prev_size = cached.get("size")
            
            # Check metadata first
            if f_mtime == prev_mtime:
                lifted = True
            elif f_size != prev_size:
                lifted = False
            else:
                # Fallback to MD5
                if use_md5_lift_fallback:
                    try:
                        content = read_project_text(project_name, rel, f)
                        f_hash = hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest() if content else None
                    except (OSError, PermissionError):
                        f_hash = None

                    if f_hash == prev_hash:
                        lifted = True
                        cached["mtime"] = f_mtime # Update mtime in cache memory
                else:
                        lifted = False
        
        if lifted:
            # LIFT
            f_data = dict(cached)
            # Keep lifted entries aligned with fresh genome output.
            if genome_data:
                f_data["dna"] = genome_data.get("dna")
                f_data["leaks"] = sorted(list(genome_data.get("leaks", [])))
                f_data["features"] = sorted(list(genome_data.get("features", [])))
                f_data["deps"] = genome_data.get("deps", {})
                f_data["symbols"] = symbol_buckets
            files.append(f_data)
            lifted_files += 1
            totals["classes"] += len(f_data["symbols"]["classes"])
            totals["functions"] += len(f_data["symbols"]["functions"])
            totals["hooks"] += len(f_data["symbols"]["hooks"])
            totals["interfaces"] += len(f_data["symbols"]["interfaces"])
            totals["types"] += len(f_data["symbols"]["types"])
            depth[len(Path(rel).parts)] += 1
            first_part = Path(rel).parts[0] if Path(rel).parts else None
            if first_part and first_part not in SKIP_DIRS:
                top_dirs[first_part] += 1
            continue
        # If not lifted, run the scan
        if not f.exists():
            missing_files.append(rel)
            logger.warning("[FRACTAL] Source disappeared during scan; skipping: %s", f)
            continue
        scanned_files += 1
        f_hash = None
        # Hashing the file body is expensive; only compute it when MD5 fallback lifting is enabled.
        if use_md5_lift_fallback:
            try:
                content = read_project_text(project_name, rel, f)
                f_hash = hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest() if content else None
            except Exception as exc:
                record_honesty_event(
                    component="fractal_mapper",
                    category="caught_error",
                    operation="md5_lift_fallback",
                    subject=str(f),
                    reason="MD5 lift fallback could not read source bytes",
                    fallback="scan_without_hash_lift",
                    claim_impact="performance_only",
                    exception=exc,
                )
                f_hash = None

        if f_mtime < 0 or f_size < 0:
            try:
                f_stat = os.stat(f)
                f_mtime = f_stat.st_mtime
                f_size = f_stat.st_size
            except Exception as exc:
                record_honesty_event(
                    component="fractal_mapper",
                    category="caught_error",
                    operation="file_stat_for_scan",
                    subject=str(f),
                    reason="file metadata unavailable during fractal scan",
                    fallback="unknown_mtime_size",
                    claim_impact="metadata_only",
                    exception=exc,
                )

        sem = 0.0
        atlas_file_entry = (atlas_files or {}).get(rel, {})
        atlas_loc = atlas_file_entry.get("loc")
        if use_atlas_semantic and isinstance(atlas_loc, (int, float)):
            sem = float(max(0.0, atlas_loc))
            semantic_from_atlas += 1
        else:
            content = read_project_text(project_name, rel, f)
            sem = float(len(COMBINED_SEMANTIC_PATTERN.findall(content)))
            semantic_from_content += 1

        f_data = {
            "path": rel,
            "semantic_total": float(sem),
            "mtime": f_mtime,
            "size": f_size,
            "hash": f_hash,
            "dna": genome_data.get("dna"),
            "leaks": sorted(list(genome_data.get("leaks", []))),
            "features": sorted(list(genome_data.get("features", []))),
            "deps": genome_data.get("deps", {}),
            "symbols": symbol_buckets,
        }
        files.append(f_data)
        totals["classes"] += len(symbol_buckets["classes"])
        totals["functions"] += len(symbol_buckets["functions"])
        totals["hooks"] += len(symbol_buckets["hooks"])
        totals["interfaces"] += len(symbol_buckets["interfaces"])
        totals["types"] += len(symbol_buckets["types"])
        depth[len(Path(rel).parts)] += 1
        first_part = Path(rel).parts[0] if Path(rel).parts else None
        if first_part and first_part not in SKIP_DIRS:
            top_dirs[first_part] += 1
    loop_elapsed = perf_counter() - loop_start
    total_elapsed = perf_counter() - project_start

    result = {
        "root": project_root.as_posix(),
        "file_count": len(files),
        "symbol_totals": dict(totals),
        "hierarchy": {"top_directories": dict(top_dirs.most_common(30)), "depth_distribution": dict(sorted(depth.items()))},
        "files": files,
    }
    result["_profile"] = {
        "lifted_files": lifted_files,
        "scanned_files": scanned_files,
        "semantic_from_atlas": semantic_from_atlas,
        "semantic_from_content": semantic_from_content,
        "missing_files": len(missing_files),
        "missing_file_sample": missing_files[:25],
        "loop_s": round(loop_elapsed, 3),
        "total_s": round(total_elapsed, 3),
    }
    return result


def normalize_genome(raw: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    out = {}
    for name, occs in raw.items():
        out[name] = []
        for occ in occs:
            x = dict(occ)
            x["project"] = normalize_project_name(str(x.get("project", "")))
            out[name].append(x)
    return out


def load_genome(root: Path) -> Dict[str, List[Dict]]:
    return normalize_genome(load_genome_data())


def normalize_coupling(raw: list[dict]) -> Dict[Tuple[str, str, str], float]:
    out: Dict[Tuple[str, str, str], float] = {}
    for e in raw:
        proj = normalize_project_name(str(e.get("project", "")))
        out[(proj, str(e.get("file", "")), str(e.get("name", "")))] = float(e.get("coupling", 0) or 0)
    return out


def load_coupling(root: Path) -> Dict[Tuple[str, str, str], float]:
    return normalize_coupling(load_json_file(RAW_DIR / "surgical_discovery.json", []))


def build_semantic_index(mapped: Dict[str, Dict]) -> Dict[Tuple[str, str], float]:
    idx = {}
    for proj, data in mapped.items():
        for f in data["files"]:
            idx[(proj, f["path"])] = float(f["semantic_total"])
    return idx


def resolve_target_context(project: str, name: str, occ: Dict) -> Dict:
    return resolve_target_studio_context(project, name, occ, CAPABILITY_KEYWORDS)


def determine_layer(name: str, occ: Dict) -> str:
    n = name.lower()
    fp = str(occ.get("file", "")).lower().replace("\\", "/")
    hex_seg = str((occ.get("meta", {}) or {}).get("hexagonal", ""))
    typ = str(occ.get("type", ""))

    assembly = require_dead_code_policy("assembly_governance")
    layer_overrides = assembly.get("layer_overrides", [])
    for rule in layer_overrides:
        fragments = rule.get("fragments", [])
        forced_layer = rule.get("layer", "features")
        if any(fragment in fp or fp.startswith(fragment.strip("/")) for fragment in fragments):
            return forced_layer

    rules = get_layer_rules()
    for rule in rules:
        layer = rule.get("layer")
        cond = rule.get("conditions", {})

        # Name prefix/suffix check
        if "name_prefix" in cond and n.startswith(cond["name_prefix"].lower()):
            return layer
        if "name_suffix" in cond:
            suffixes = cond["name_suffix"]
            if isinstance(suffixes, str): suffixes = [suffixes]
            if any(n.endswith(s.lower()) for s in suffixes):
                return layer

        # Path fragments check
        if "path_fragments" in cond:
            if any(f.lower() in fp for f in cond["path_fragments"]):
                return layer

        # Hexagonal meta match
        if "hexagonal_meta" in cond:
            if any(h.lower() in hex_seg.lower() for h in cond["hexagonal_meta"]):
                return layer

        # Type match
        if "type_match" in cond:
            if cond["type_match"].lower() in typ.lower():
                return layer

    return "features"


def choose_target_extension(name: str, layer: str, occ: Dict) -> str:
    source_path = str(occ.get("file", "")).lower().replace("\\", "/")
    mapping_doctrine = require_doctrine_mapping("fractal_mapping_doctrine")
    path_output = mapping_doctrine.get("path_output")
    default_ext = str(path_output.get("default_extension") or ".ts")
    ui_ext = str(path_output.get("ui_extension") or ".tsx")
    component_layers = set(path_output.get("component_layers") or ["pages", "widgets", "features"])
    source_ext = Path(source_path).suffix or default_ext

    layer_extensions = mapping_doctrine.get("layer_extensions")
    naming = require_dead_code_policy("naming_doctrine")

    if layer in layer_extensions:
        ext = layer_extensions[layer]
        if ext:
            return str(ext)

    if layer in component_layers:
        if source_ext == ui_ext:
            return ui_ext
        if naming.get("pascal_tsx_standard") and name[:1].isupper():
            return ui_ext

    return str(layer_extensions.get("default") or default_ext)


def _fractal_default_extension() -> str:
    return str(require_doctrine_mapping("fractal_mapping_doctrine").get("path_output").get("default_extension"))


def _fractal_index_file() -> str:
    return str(require_doctrine_mapping("fractal_mapping_doctrine").get("path_output").get("index_file"))


def _with_default_extension(path_without_ext: str) -> str:
    return f"{path_without_ext}{_fractal_default_extension()}"


def _index_under(path_without_file: str) -> str:
    return f"{path_without_file.rstrip('/')}/{_fractal_index_file()}"


def normalize_target_symbol_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return raw

    if raw.startswith("proxy:"):
        proxy_target = raw.split(":", 1)[1].strip()
        proxy_target = proxy_target.replace("\\", "/")
        raw = Path(proxy_target).stem

    raw = raw.replace("type ", "").replace("interface ", "").strip()
    return Path(raw).stem if any(sep in raw for sep in ("/", "\\")) else raw


def classify_target_contract_shape(name: str, occ: Dict) -> str:
    normalized_name = normalize_target_symbol_name(name)
    lowered = normalized_name.lower()
    source_path = str(occ.get("file", "")).lower().replace("\\", "/")
    occ_type = str(occ.get("type", "") or "").lower()

    if "prompt" in lowered and ("/server/" in source_path or "/flows/" in source_path):
        return "server_prompt"
    if "/types/schemas/" in source_path or source_path.startswith("types/schemas/"):
        return "schema"
    if re.fullmatch(r"[A-Z0-9_]+", normalized_name or ""):
        return "constant"
    if occ_type in {"interface", "type", "typedefinition"}:
        return "type_contract"
    if "schema" in lowered:
        return "schema"
    return "default"


def refine_target_contract(name: str, layer: str, occ: Dict) -> Tuple[str, str, float]:
    normalized_name = normalize_target_symbol_name(name)
    contract_shape = classify_target_contract_shape(name, occ)

    if contract_shape == "server_prompt":
        return normalized_name, "application", 0.2
    if contract_shape == "constant":
        return normalized_name, "shared/constants", 0.2
    if contract_shape == "type_contract":
        return normalized_name, "shared/contracts", 0.2
    if contract_shape == "schema":
        return normalized_name, "shared/model", 0.2
    return normalized_name, layer, 0.5


def sanitize_target_path(target: str) -> str:
    cleaned = (target or "").replace("\\", "/")
    cleaned = cleaned.replace("proxy:./", "").replace("proxy:", "")
    cleaned = re.sub(r"\.(tsx|ts|jsx|js|mts|cts|mjs|cjs)\.(tsx|ts|jsx|js|mts|cts|mjs|cjs)$", r".\1", cleaned)
    cleaned = cleaned.replace("//", "/")
    return cleaned


def _path_exists(relative_path: str) -> bool:
    return (ROOT / relative_path.replace("/", os.sep)).exists()


def _pick_existing_home(candidates: List[str], fallback: str) -> str:
    for candidate in candidates:
        if _path_exists(candidate):
            return candidate.rstrip("/")
    return fallback.rstrip("/")


def _relative_to_root(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _schema_target_cache() -> Dict[Tuple[str, str], Tuple[str, str]]:
    return GLOBAL_FRACTAL_CACHE.setdefault("schema_target_lookup", {})


def _schema_search_entries() -> List[Tuple[str, str, str, str, str]]:
    cached = GLOBAL_FRACTAL_CACHE.get("schema_search_entries")
    if cached is not None:
        return cached

    entries: List[Tuple[str, str, str, str, str]] = []
    mod_root = get_module_container()
    atlas_raw = load_atlas_data()
    main_files = ((atlas_raw.get("MAIN") or {}).get("files") or {}) if isinstance(atlas_raw, dict) else {}
    for rel_path in sorted(main_files):
        file_path = MAIN_PROJECT_ROOT / str(rel_path).replace("\\", "/")
        rel = _relative_to_root(file_path)
        rel_lower = rel.lower()
        if (
            "/domain/schemas/" not in rel_lower
            and "/shared/model/" not in rel_lower
            and "/shared/kernel/" not in rel_lower
            and "/infra/schemas/" not in rel_lower
        ):
            continue

        path_parts = rel.replace("\\", "/").split("/")
        gateway_path = get_platform_default_path()
        gateway = _index_under(gateway_path) if "." not in gateway_path else gateway_path
        if mod_root in path_parts:
            idx = path_parts.index(mod_root)
            if len(path_parts) > idx + 1:
                module_name = path_parts[idx + 1]
                gateway = _index_under(f"src/{mod_root}/{module_name}/api")

        text_lower = read_project_text("MAIN", str(rel_path).replace("\\", "/"), file_path).lower()
        entries.append((rel, rel_lower, Path(rel_lower).name, text_lower, sanitize_target_path(gateway)))

    GLOBAL_FRACTAL_CACHE["schema_search_entries"] = entries
    return entries


def find_existing_host_schema_target(name: str, occ: Dict) -> Tuple[str, str] | None:
    normalized_name = normalize_target_symbol_name(name)
    source_path = str(occ.get("file", "") or "").replace("\\", "/")
    source_basename = Path(source_path).name.lower()
    cache_key = (normalized_name.lower(), source_basename)
    cache = _schema_target_cache()
    if cache_key in cache:
        return cache[cache_key]

    matches: List[Tuple[str, str, int]] = []
    candidate_symbols = {normalized_name.lower()}
    if not normalized_name.lower().endswith("schema"):
        candidate_symbols.add(f"{normalized_name.lower()}schema")

    for rel, rel_lower, rel_basename, text_lower, gateway in _schema_search_entries():
        basename_score = 0
        if source_basename and rel_lower.endswith("/" + source_basename):
            basename_score = 3
        elif source_basename and rel_basename == source_basename:
            basename_score = 2

        if not any(symbol in text_lower for symbol in candidate_symbols):
            continue

        if basename_score == 0:
            basename_score = 1

        matches.append((rel, gateway, basename_score))

    if not matches:
        cache[cache_key] = None
        return None

    matches.sort(key=lambda item: (-item[2], len(item[0]), item[0]))
    winner = (sanitize_target_path(matches[0][0]), sanitize_target_path(matches[0][1]))
    cache[cache_key] = winner
    return winner


def platform_path(name: str, occ: Dict | None = None) -> str:
    n = name.lower()
    source_path = str((occ or {}).get("file", "")).lower().replace("\\", "/")

    mapping_doctrine = require_doctrine_mapping("fractal_mapping_doctrine")
    platform_rules = mapping_doctrine.get("platform_routing_rules")

    for rule in platform_rules:
        # Check source_contains if present
        src_contains = rule.get("source_contains")
        if src_contains:
            if isinstance(src_contains, str):
                src_contains = [src_contains]
            if not any(token in source_path for token in src_contains):
                continue

        # Check source_match_or_start if present
        src_match_or_start = rule.get("source_match_or_start")
        if src_match_or_start:
            if isinstance(src_match_or_start, str):
                src_match_or_start = [src_match_or_start]
            if not any(source_path.startswith(token) or token in source_path for token in src_match_or_start):
                continue

        # Check name_contains if present
        name_contains = rule.get("name_contains")
        if name_contains:
            if isinstance(name_contains, str):
                name_contains = [name_contains]
            if not any(token in n for token in name_contains):
                continue

        homes = rule.get("homes", ["src/platform/core"])
        default_home = rule.get("default_home", "src/platform/core")
        base = _pick_existing_home(homes, default_home)
        return _with_default_extension(f"{base}/{name}")

    assembly = require_dead_code_policy("assembly_governance")
    source_family_rules = assembly.get("family_routing", [])
    for rule in source_family_rules:
        fragments = rule.get("fragments", [])
        base = rule.get("target", "src/platform/core/")
        if any(fragment in source_path or source_path.startswith(fragment.strip("/")) for fragment in fragments):
            return _with_default_extension(f"{base}{name}")

    mapping = get_platform_mapping()
    for rule in mapping:
        if rule["keyword"].lower() in n:
            return _with_default_extension(f"{rule['path']}{name}")

    default_base = get_platform_default_path()
    return _with_default_extension(f"{default_base}{name}")


def platform_shared_path(name: str, effective_layer: str, occ: Dict) -> Tuple[str, str]:
    contract_shape = classify_target_contract_shape(name, occ)
    source_path = str(occ.get("file", "")).lower().replace("\\", "/")

    mapping_doctrine = require_doctrine_mapping("fractal_mapping_doctrine")
    shared_rules = mapping_doctrine.get("shared_routing_rules")

    for rule in shared_rules:
        if rule.get("effective_layer") == effective_layer:
            conditions = rule.get("conditions", [])
            for cond in conditions:
                # check source_contains if present
                src_contains = cond.get("source_contains")
                if src_contains and src_contains not in source_path:
                    continue

                # check source_contains_any if present
                src_contains_any = cond.get("source_contains_any")
                if src_contains_any:
                    if not any(token in source_path for token in src_contains_any):
                        continue

                # check contract_shape if present
                c_shape = cond.get("contract_shape")
                if c_shape and c_shape != contract_shape:
                    continue

                homes = cond.get("homes", [])
                default_home = cond.get("default_home")
                base = _pick_existing_home(homes, default_home)
                return _with_default_extension(f"{base}/{name}"), _index_under(base)

    # Default fallback
    base = _pick_existing_home(
        [f"src/shared/{effective_layer.replace('shared/', '')}"],
        f"src/shared/{effective_layer.replace('shared/', '')}",
    )
    return _with_default_extension(f"{base}/{name}"), _index_under(base)



def suggest_target(name: str, ctx: Dict, layer: str, occ: Dict) -> Tuple[str, str, bool, str]:
    st = ctx["target_studio"]
    conf = float(ctx["confidence"])
    normalized_name, effective_layer, contract_conf_threshold = refine_target_contract(name, layer, occ)
    ext = choose_target_extension(normalized_name, effective_layer, occ)
    source_path = str(occ.get("file", "")).lower().replace("\\", "/")
    source_routed_platform = (
        st == PLATFORM_CORE_STUDIO
        and effective_layer in {"application", "domain/logic", "infra", "features", "shared/hooks", "shared/utils"}
        and (
            "/services/" in source_path
            or "/workers/" in source_path
            or "/server/" in source_path
            or "/utils/" in source_path
            or "/entities/" in source_path
            or source_path.startswith("entities/")
            or source_path.startswith("utils/")
            or source_path.startswith("workers/")
            or "service-worker" in source_path
            or "/contexts/" in source_path
            or "/components/" in source_path
            or source_path.startswith("contexts/")
            or source_path.startswith("components/")
            or source_path.startswith("hooks/")
        )
    )
    if source_routed_platform:
        contract_conf_threshold = min(contract_conf_threshold, 0.4)

    if classify_target_contract_shape(name, occ) == "schema":
        existing_schema_target = find_existing_host_schema_target(normalized_name, occ)
        if existing_schema_target:
            target, gateway = existing_schema_target
            return target, gateway, conf >= contract_conf_threshold, effective_layer

    # Generated targets are workspace-relative; source roots and module containers
    # are distinct contracts when MAIN itself is already registered as `src`.
    base_prefix = get_main_module_base_prefix()

    if st == PLATFORM_CORE_STUDIO:
        if effective_layer.startswith("shared/"):
            target, gateway = platform_shared_path(normalized_name, effective_layer, occ)
            return sanitize_target_path(target), sanitize_target_path(gateway), conf >= contract_conf_threshold, effective_layer
        target = platform_path(normalized_name, occ).rsplit(".", 1)[0] + ext
        gateway_path = get_platform_default_path()
        final_gateway = _index_under(gateway_path) if "." not in gateway_path else gateway_path
        return sanitize_target_path(target), sanitize_target_path(final_gateway), conf >= contract_conf_threshold, effective_layer

    module = STUDIO_TO_MODULE.get(st)
    if not module:
        target = platform_path(normalized_name, occ).rsplit(".", 1)[0] + ext
        gateway_path = get_platform_default_path()
        final_gateway = _index_under(gateway_path) if "." not in gateway_path else gateway_path
        return sanitize_target_path(target), sanitize_target_path(final_gateway), False, effective_layer

    if effective_layer == "api":
        target = _with_default_extension(f"{base_prefix}/{module}/api/{normalized_name}")
    elif effective_layer == "domain/logic":
        target = _with_default_extension(f"{base_prefix}/{module}/domain/logic/{normalized_name}")
    else:
        target = f"{base_prefix}/{module}/{effective_layer}/{normalized_name}{ext}"

    return sanitize_target_path(target), sanitize_target_path(_index_under(f"{base_prefix}/{module}/api")), conf >= contract_conf_threshold, effective_layer


def score_occurrence(occ: Dict, sem: float, coupling: float, layer: str) -> float:
    density = float(occ.get("density", 0) or 0)
    dec = float(occ.get("decoupling", 80) or 80)
    leaks = occ.get("leaks", []) or []
    has_tests = occ.get("has_tests", False)

    # Use analysis_heuristics for scoring
    heuristics = require_doctrine_mapping("analysis_heuristics")
    cfg = heuristics.get("complexity_scoring")
    penalties = dict(cfg.get("coupling_penalties"))
    penalty_weight = penalties.get(layer, penalties["default"])
    coupling_penalty = penalty_weight * coupling

    test_bonus = cfg.get("test_bonus") if has_tests else 0.0

    # Base formula weights from config
    leaks_w = cfg.get("leaks_penalty_weight")
    density_w = cfg.get("density_weight")
    dec_w = cfg.get("decoupling_weight")
    sem_w = cfg.get("semantic_weight")

    # Sovereign Refinement: Potential Healed Score
    healing_rules = require_doctrine_mapping("governance_policy").get("healing_rules")
    leak_to_rule_map = {
        "RELATIVE_PARENT_ESCAPE": "relative_imports_no_alias",
        "Banned:i18n": "banned_i18n"
    }
    
    healable_leaks = [l for l in leaks if leak_to_rule_map.get(l) in healing_rules]
    fatal_leaks = [l for l in leaks if l not in healable_leaks]
    
    base_score = (density * density_w) + (dec * dec_w) + (sem * sem_w) - coupling_penalty + test_bonus
    current_score = base_score - (leaks_w * len(leaks))
    potential_healed_score = base_score - (leaks_w * len(fatal_leaks))

    return round(max(current_score, potential_healed_score * 0.9), 2)


def classify_risk(occ: Dict, delta: float, coupling: float, conf: float) -> str:
    dec = float(occ.get("decoupling", 80) or 80)
    leaks = len(occ.get("leaks", []) or [])
    
    risk_policy = require_doctrine_mapping("dashboard_risk_policy")
    penalty_cfg = risk_policy.get("penalty_coefficients")
    penalty = (100 - dec) * penalty_cfg.get("decoupling") + \
              leaks * penalty_cfg.get("leaks") + \
              coupling * penalty_cfg.get("coupling") + \
              (1 - conf) * penalty_cfg.get("confidence")
              
    heuristics = require_dead_code_policy("assembly_governance").get("decision_heuristics")
    if delta > 40 and penalty < 20:
        return "LOW"
    if delta > 12 and penalty < 45:
        return "MEDIUM"
    return "HIGH"


def should_keep_main_first(bucket: str, main_score: float, var_score: float, confidence: float) -> bool:
    heuristics = require_dead_code_policy("assembly_governance").get("decision_heuristics")
    if bucket == "general":
        return False
    if main_score <= 0:
        return False
    if var_score < (main_score * heuristics.get("main_retention_var_multiplier")):
        return True
    if (var_score - main_score) < heuristics.get("main_retention_score_gap"):
        return True
    if confidence < heuristics.get("candidate_confidence_threshold"):
        return True
    return False


def export_recommendation(name: str, layer: str, target_studio: str, confidence: float, action: str) -> Tuple[str, str]:
    n = name.lower()
    if action == "keep_main":
        return "no_change", "main implementation retained"

    if layer in {"application", "domain/logic", "infra", "api"}:
        if target_studio == PLATFORM_CORE_STUDIO or any(x in n for x in ["service", "manager", "orchestrator", "adapter", "repository"]):
            return "public_api_candidate", "cross-studio or platform-facing logic"
        if confidence >= 0.75:
            return "public_api_candidate", "high-confidence studio-level capability"

    return "local_internal", "keep internal until cross-studio need is proven"


def _merge_readiness_policy() -> Dict:
    return require_dead_code_policy("assembly_governance")["merge_readiness_policy"]


def _risk_rank(row: Dict) -> int:
    policy = _merge_readiness_policy()
    return int(policy["risk_rank"].get(str(row.get("risk") or ""), len(policy["risk_rank"])))


def assess_merge_readiness(row: Dict) -> Tuple[str, List[str]]:
    reasons: List[str] = []

    confidence = float(row.get("confidence", 0) or 0)
    risk = str(row.get("risk") or "")
    readiness_policy = _merge_readiness_policy()
    target_studio = str(row.get("target_studio", ""))
    target_layer = str(row.get("target_layer", ""))
    source_path = str(row.get("source_path", "")).lower().replace("\\", "/")
    target_path = str(row.get("target_path_suggestion", "")).lower().replace("\\", "/")
    name = str(row.get("name", ""))
    source_project = str(row.get("source_project", "")).strip()
    target_contract_shape = classify_target_contract_shape(name, row)
    actual_feature_target = "/features/" in target_path
    actual_ui_target = actual_feature_target or "/components/" in target_path or "/ui/" in target_path
    workspace_mode = get_workspace_mode()
    host_projects = workspace_mode.get("host_projects", []) or ["MAIN"]
    target_host = str(host_projects[0] if host_projects else "MAIN")
    source_allowed, source_reason = is_source_allowed_for_host_merge(source_project=source_project, target_project=target_host)
    if not source_allowed:
        reasons.append(source_reason)

    if confidence < float(readiness_policy["minimum_auto_merge_confidence"]):
        reasons.append("low_target_confidence")
    if risk not in set(readiness_policy["auto_merge_allowed_risks"]):
        reasons.append("risk_tier_requires_review")
    if not row.get("fractal_contract_ok", False):
        reasons.append("target_contract_not_proven")
    if target_studio == PLATFORM_CORE_STUDIO and target_layer == "features" and actual_feature_target:
        reasons.append("platform_feature_target_requires_review")
    if (
        source_path.startswith("app/")
        or source_path.endswith("/page.tsx")
        or source_path.endswith("/layout.tsx")
        or "/routes/" in source_path
    ):
        reasons.append("app_shell_source_requires_review")
    if (
        ("/services/inspiration/" in source_path or source_path.startswith("services/inspiration/"))
        and "src/platform/analysis/" in target_path
        and name.endswith("Service")
    ):
        reasons.append("analysis_service_target_requires_review")
    if actual_ui_target and target_layer == "features" and name[:1].isupper() and confidence < 0.7:
        reasons.append("ui_surface_without_strong_match")
    if (
        target_contract_shape in {"schema", "type_contract", "constant", "server_prompt"}
        and row.get("fractal_contract_ok", False)
        and confidence >= 0.75
    ):
        reasons = [reason for reason in reasons if reason not in {"low_target_confidence", "target_contract_not_proven"}]
    if (
        target_layer in {"shared/contracts", "shared/model", "shared/constants"}
        and row.get("fractal_contract_ok", False)
    ):
        reasons = [reason for reason in reasons if reason != "low_target_confidence"]
    if (
        target_layer == "shared/utils"
        and row.get("fractal_contract_ok", False)
    ):
        reasons = [reason for reason in reasons if reason != "low_target_confidence"]
    if (
        target_layer == "shared/hooks"
        and name.startswith("use")
        and "src/shared/hooks/" in target_path
    ):
        reasons = [reason for reason in reasons if reason not in {"low_target_confidence", "target_contract_not_proven"}]
    if (
        target_layer == "shared/constants"
        and "src/shared/constants/" in target_path
        and ("/constants/templates/" in source_path or source_path.startswith("constants/templates/"))
    ):
        reasons = [reason for reason in reasons if reason not in {"low_target_confidence", "target_contract_not_proven"}]
    if (
        target_layer == "shared/model"
        and "src/shared/types/schemas/" in target_path
        and ("/types/schemas/" in source_path or source_path.startswith("types/schemas/"))
    ):
        reasons = [reason for reason in reasons if reason not in {"low_target_confidence", "target_contract_not_proven"}]
    if (
        target_layer == "shared/model"
        and "src/shared/types/" in target_path
        and ("/shared/model/" in source_path or source_path.startswith("shared/model/"))
    ):
        reasons = [reason for reason in reasons if reason not in {"low_target_confidence", "target_contract_not_proven"}]
    if (
        target_layer == "shared/ui"
        and "src/shared/ui/" in target_path
    ):
        reasons = [reason for reason in reasons if reason not in {"low_target_confidence", "target_contract_not_proven"}]
    if (
        target_layer == "shared/contracts"
        and "src/shared/types/" in target_path
        and ("/types/domain/" in source_path or source_path.startswith("types/domain/"))
    ):
        reasons = [reason for reason in reasons if reason not in {"low_target_confidence", "target_contract_not_proven"}]
    if (
        target_layer == "domain/logic"
        and "src/platform/core/domain/" in target_path
        and (
            "/entities/" in source_path
            or source_path.startswith("entities/")
            or "/shared/model/" in source_path
            or source_path.startswith("shared/model/")
        )
    ):
        reasons = [reason for reason in reasons if reason not in {"low_target_confidence", "target_contract_not_proven"}]
    if "/routes/" in source_path:
        reasons = [reason for reason in reasons if reason not in {"low_target_confidence", "target_contract_not_proven"}]
    if (
        ("/services/inspiration/" in source_path or source_path.startswith("services/inspiration/"))
        and "src/platform/analysis/" in target_path
        and name.endswith("Service")
    ):
        reasons = [reason for reason in reasons if reason not in {"low_target_confidence", "target_contract_not_proven"}]
    if (
        row.get("fractal_contract_ok", False)
        and (
            "src/platform/ai/" in target_path
            or "src/platform/export/" in target_path
            or "src/platform/storage/" in target_path
            or "src/platform/assets/" in target_path
            or "src/platform/analysis/" in target_path
            or "src/platform/auth/" in target_path
            or "src/platform/sync/" in target_path
            or "src/platform/networking/" in target_path
            or "src/platform/maintenance/" in target_path
            or "src/platform/strategies/" in target_path
            or "src/platform/repositories/" in target_path
            or "src/platform/validators/" in target_path
            or "src/platform/ui/" in target_path
            or "src/platform/hooks/" in target_path
        )
    ):
        reasons = [reason for reason in reasons if reason != "low_target_confidence"]

    # Context-specific exemptions may remove soft targeting concerns, but they
    # cannot override the central release-safety floor.
    if confidence < float(readiness_policy["minimum_auto_merge_confidence"]):
        if "low_target_confidence" not in reasons:
            reasons.append("low_target_confidence")
    if risk not in set(readiness_policy["auto_merge_allowed_risks"]):
        if "risk_tier_requires_review" not in reasons:
            reasons.append("risk_tier_requires_review")

    return ("manual_review", reasons) if reasons else ("auto_merge", [])


def classify_review_bucket(review_reasons: List[str]) -> str:
    reasons = set(review_reasons or [])
    if not reasons:
        return "auto_merge_ready"
    if "companion_source_disallowed" in reasons:
        return "source_role_policy"

    architecture_reasons = {
        "platform_feature_target_requires_review",
        "app_shell_source_requires_review",
        "ui_surface_without_strong_match",
        "analysis_service_target_requires_review",
    }
    if reasons.issubset(architecture_reasons):
        return "hitl_architecture_review"
    return "target_contract_weak"


def canonical_name(name: str) -> str:
    naming = require_dead_code_policy("naming_doctrine")
    prefixes = "|".join(naming.get("strip_prefixes"))
    suffixes = "|".join(naming.get("strip_suffixes"))
    
    n = re.sub(rf"^({prefixes})", "", name, flags=re.IGNORECASE)
    n = re.sub(rf"({suffixes})$", "", n, flags=re.IGNORECASE)
    return n.lower() or name.lower()


def is_proxy_export_symbol(name: str, occs: List[Dict]) -> bool:
    clean = (name or "").strip()
    if clean.startswith("proxy:"):
        return True
    types = {str(o.get("type", "") or "") for o in occs}
    return bool(types) and types.issubset({"ProxyExport"})


def is_significant_merge_symbol(name: str, occs: List[Dict]) -> bool:
    clean = (name or "").strip()
    if not clean:
        return False

    if is_proxy_export_symbol(name, occs):
        return False

    lowered = clean.lower()
    if lowered in LOW_SIGNAL_NAME_STOPLIST:
        return False

    has_hook_shape = clean.startswith("use")
    has_pascal_shape = clean[:1].isupper()
    has_service_shape = any(
        token in clean
        for token in ["Service", "Manager", "Adapter", "Repository", "Orchestrator", "Provider", "Store"]
    )

    if len(clean) == 1:
        return False

    if len(clean) <= 2 and not (has_hook_shape or has_pascal_shape or has_service_shape):
        return False

    if len(clean) <= 3 and lowered == clean and not (has_hook_shape or has_service_shape):
        return False

    types = {str(o.get("type", "")) for o in occs}
    files = [str(o.get("file", "")).lower().replace("\\", "/") for o in occs]

    if types and types.issubset({"Variable", "ExportedVariable", "Type", "TypeDefinition"}) and len(clean) <= 4 and not has_service_shape:
        return False

    if any("/test" in fp or ".spec." in fp or ".test." in fp for fp in files):
        return False
    if any(fp.endswith(".d.ts") or fp.endswith(".d.tsx") for fp in files):
        return False
    if all(fp.endswith(("index.ts", "index.tsx", "main.ts", "main.tsx")) for fp in files):
        if not (has_pascal_shape or has_hook_shape or has_service_shape):
            return False

    generic_single_token = re.fullmatch(r"[a-z][a-z0-9_]*", clean or "") is not None
    localish_source = all(
        any(fragment in fp for fragment in ["/server/", "/utils/", "/constants/", "/index.tsx", "/index.ts", "/lib/"])
        for fp in files
    )
    if generic_single_token and localish_source and not (has_hook_shape or has_service_shape):
        return False

    return True


def build_decisions(genome: Dict[str, List[Dict]], sem_idx: Dict[Tuple[str, str], float], coup_idx: Dict[Tuple[str, str, str], float]) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    decisions = []
    candidate_pool = []
    manual_review_pool = []

    for name, occs in genome.items():
        if not is_significant_merge_symbol(name, occs):
            continue
        main_occs = []
        var_occs = []
        for o in occs:
            project_name = normalize_project_name(str(o.get("project", "")))
            if project_name == "MAIN":
                main_occs.append(o)
            else:
                var_occs.append(o)
        if not var_occs:
            continue

        layer_cache = {}

        def layer_for(occ: Dict) -> str:
            occ_key = (
                str(occ.get("file", "")),
                str(occ.get("type", "")),
                str((occ.get("meta", {}) or {}).get("hexagonal", "")),
            )
            cached = layer_cache.get(occ_key)
            if cached:
                return cached
            resolved = determine_layer(name, occ)
            layer_cache[occ_key] = resolved
            return resolved

        best_main_score = 0.0
        if main_occs:
            best_main_score = max(
                score_occurrence(
                    m, 
                    sem_idx.get(("MAIN", str(m.get("file", ""))), 0.0), 
                    coup_idx.get(("MAIN", str(m.get("file", "")), name), 0.0),
                    layer_for(m)
                )
                for m in main_occs
            )

        scored = []
        for v in var_occs:
            proj = normalize_project_name(str(v.get("project", "")))
            fp = str(v.get("file", ""))
            sem = sem_idx.get((proj, fp), 0.0)
            coup = coup_idx.get((proj, fp, name), 0.0)
            layer = layer_for(v)
            s = score_occurrence(v, sem, coup, layer)
            mtime = float(v.get("mtime", 0))
            scored.append((s, v, proj, sem, coup, layer, mtime))

        # Tie-breaker logic: Primary = Score, Secondary = Modification Date (Latest Wins)
        scored.sort(key=lambda x: (x[0], x[6]), reverse=True)
        v_score, v, proj, sem, coup, layer, v_mtime = scored[0]
        ctx = resolve_target_context(proj, name, v)
        target, gateway, fractal_ok, layer = suggest_target(name, ctx, layer, v)

        bucket = domain_bucket(name, str(v.get("file", "")))
        keep_main = should_keep_main_first(bucket, best_main_score, v_score, ctx["confidence"])

        heuristics = require_dead_code_policy("assembly_governance").get("decision_heuristics")
        if keep_main:
            action = "keep_main"
            chosen = "MAIN"
            delta = v_score - best_main_score
            risk = "LOW"
            target = "MAIN_RETENTION"
            gateway = "MAIN_RETENTION"
            fractal_ok = True
        else:
            if not main_occs and v_score >= heuristics.get("new_atom_min_score"):
                action = "new_atom"
            elif main_occs and v_score > (best_main_score * heuristics.get("evo_upgrade_multiplier")) and (v_score - best_main_score) > heuristics.get("evo_upgrade_min_delta"):
                action = "evo_upgrade"
            else:
                action = "skip"
            chosen = proj
            delta = v_score - best_main_score
            risk = classify_risk(v, delta, coup, ctx["confidence"])

        if action == "skip":
            continue

        export_mode, export_reason = export_recommendation(name, layer, ctx["target_studio"], ctx["confidence"], action)

        row = {
            "name": name,
            "display_name": normalize_target_symbol_name(name),
            "action": action,
            "chosen_source": chosen,
            "source_project": proj,
            "source_path": str(v.get("file", "")),
            "source_scoped_path": f"{proj}::{str(v.get('file', ''))}",
            "source_studio": ctx["source_studio"],
            "target_studio": ctx["target_studio"],
            "target_layer": layer,
            "target_path_suggestion": target,
            "gateway_entry": gateway,
            "fractal_contract_ok": fractal_ok,
            "confidence": ctx["confidence"],
            "variation_score": round(v_score, 2),
            "main_score": round(best_main_score, 2),
            "delta": round(delta, 2),
            "risk": risk,
            "main_first_bucket": bucket,
            "export_recommendation": export_mode,
            "export_reason": export_reason,
        }

        merge_readiness, review_reasons = assess_merge_readiness(row)
        row["merge_readiness"] = merge_readiness
        row["review_reasons"] = review_reasons
        row["review_bucket"] = classify_review_bucket(review_reasons)
        decisions.append(row)

        if action in {"new_atom", "evo_upgrade"}:
            if merge_readiness == "auto_merge":
                candidate_pool.append(row)
            else:
                manual_review_pool.append(row)

    decisions.sort(key=lambda x: (x["action"], _risk_rank(x), -x["delta"]))
    candidate_pool.sort(key=lambda x: (_risk_rank(x), -x["delta"], -x["confidence"]))
    manual_review_pool.sort(key=lambda x: (-_risk_rank(x), -x["delta"], -x["confidence"]))
    return decisions, candidate_pool, manual_review_pool


def deduplicate_candidates(candidates: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    groups = defaultdict(list)
    for c in candidates:
        key = (c["target_studio"], c["target_layer"], canonical_name(c["name"]))
        groups[key].append(c)

    winners = []
    duplicates = []
    for key, items in groups.items():
        items_sorted = sorted(items, key=lambda x: (_risk_rank(x), -x["delta"], -x["confidence"]))
        winner = dict(items_sorted[0])
        winner["duplicate_group_id"] = f"{key[0]}::{key[1]}::{key[2]}"
        winner["selected_winner"] = True
        winner["merge_strategy"] = "compose_in_winner" if len(items_sorted) > 1 else "single_source"
        winners.append(winner)

        for d in items_sorted[1:]:
            x = dict(d)
            x["duplicate_group_id"] = winner["duplicate_group_id"]
            x["selected_winner"] = False
            x["suppressed_by"] = winner["name"]
            x["display_suppressed_by"] = winner.get("display_name") or winner["name"]
            duplicates.append(x)

    winners.sort(key=lambda x: (_risk_rank(x), -x["delta"], -x["confidence"]))
    duplicates.sort(key=lambda x: (x["duplicate_group_id"], -x["delta"]))
    return winners, duplicates


def build_waves(winners: List[Dict], manual_review_pool: List[Dict]) -> Dict[str, List[Dict]]:
    waves = {"wave_1_low": [], "wave_2_medium": [], "wave_3_high": [], "manual_review": []}
    for c in winners:
        item = {
            "name": c["name"], "source_project": c["source_project"], "target": c["target_path_suggestion"],
            "source_scoped_path": c.get("source_scoped_path") or f"{c.get('source_project', 'UNKNOWN')}::{c.get('source_path', '')}",
            "target_studio": c["target_studio"], "target_layer": c["target_layer"], "gateway": c["gateway_entry"],
            "action": c["action"], "delta": c["delta"], "confidence": c["confidence"],
            "export_recommendation": c["export_recommendation"], "merge_strategy": c.get("merge_strategy", "single_source"),
            "review_reasons": c.get("review_reasons", []),
            "review_bucket": c.get("review_bucket", "auto_merge_ready"),
        }
        if c["risk"] == "LOW":
            waves["wave_1_low"].append(item)
        elif c["risk"] == "MEDIUM":
            waves["wave_2_medium"].append(item)
        else:
            waves["wave_3_high"].append(item)

    for c in manual_review_pool:
        waves["manual_review"].append(
            {
                "name": c["name"],
                "source_project": c["source_project"],
                "source_scoped_path": c.get("source_scoped_path") or f"{c.get('source_project', 'UNKNOWN')}::{c.get('source_path', '')}",
                "target": c["target_path_suggestion"],
                "target_studio": c["target_studio"],
                "target_layer": c["target_layer"],
                "gateway": c["gateway_entry"],
                "action": c["action"],
                "delta": c["delta"],
                "confidence": c["confidence"],
                "export_recommendation": c["export_recommendation"],
                "merge_strategy": "manual_review_only",
                "review_reasons": c.get("review_reasons", []),
                "review_bucket": c.get("review_bucket", "target_contract_weak"),
            }
        )

    for k in waves:
        waves[k] = sorted(waves[k], key=lambda x: x["delta"], reverse=True)[:220]
    return waves


def prepare_missing_dirs(root: Path, waves: Dict[str, List[Dict]]) -> List[str]:
    required = set()
    for w in ("wave_1_low", "wave_2_medium"):
        for item in waves[w]:
            t = item["target"]
            if t == "MAIN_RETENTION":
                continue
            required.add(str(Path(t).parent).replace("\\", "/"))
    return sorted([d for d in required if not (root / d).exists()])


def detect_capability(name: str, file_path: str) -> Tuple[str, int]:
    haystack = f"{name} {file_path}".lower()
    best_capability = "uncategorized"
    best_hits = 0

    for capability, keywords in CAPABILITY_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if str(keyword).lower() in haystack)
        if hits > best_hits:
            best_capability = capability
            best_hits = hits

    return best_capability, best_hits


def build_capability_winners(genome: Dict[str, List[Dict]], sem_idx: Dict[Tuple[str, str], float], coup_idx: Dict[Tuple[str, str, str], float]) -> Dict:
    scores = defaultdict(lambda: defaultdict(float))
    counts = defaultdict(lambda: defaultdict(int))
    for name, occs in genome.items():
        if not is_significant_merge_symbol(name, occs):
            continue
        for o in occs:
            proj = normalize_project_name(str(o.get("project", "")))
            if proj == "MAIN":
                continue
            fp = str(o.get("file", ""))
            cap, hits = detect_capability(name, fp)
            if hits == 0:
                continue
            
            layer = determine_layer(name, o)
            s = score_occurrence(o, sem_idx.get((proj, fp), 0.0), coup_idx.get((proj, fp, name), 0.0), layer)
            scores[cap][proj] += s
            counts[cap][proj] += 1

    out = {}
    for cap, row in scores.items():
        ranked = sorted(row.items(), key=lambda x: x[1], reverse=True)
        out[cap] = {
            "winner": ranked[0][0] if ranked else None,
            "ranking": [{"project": p, "score": round(v, 2), "items": counts[cap].get(p, 0)} for p, v in ranked[:5]],
        }
    return out


def write_outputs(root: Path, mapped: Dict[str, Dict], winners_by_cap: Dict, decisions: List[Dict], dedup_winners: List[Dict], dedup_suppressed: List[Dict], manual_review_pool: List[Dict], waves: Dict[str, List[Dict]], missing_dirs: List[str], run_meta: Dict | None = None):
    meta_payload = {
        "decision_count": len(decisions),
        "candidate_count": len(dedup_winners),
        "suppressed_duplicates": len(dedup_suppressed),
        "keep_main_count": sum(1 for d in decisions if d["action"] == "keep_main"),
        "low": len(waves["wave_1_low"]),
        "medium": len(waves["wave_2_medium"]),
        "high": len(waves["wave_3_high"]),
        "manual_review": len(manual_review_pool),
        "manual_review_wave": len(waves["manual_review"]),
        "manual_review_target_contract_weak": sum(1 for c in manual_review_pool if c.get("review_bucket") == "target_contract_weak"),
        "manual_review_hitl_architecture_review": sum(1 for c in manual_review_pool if c.get("review_bucket") == "hitl_architecture_review"),
        "manual_review_source_role_policy": sum(1 for c in manual_review_pool if c.get("review_bucket") == "source_role_policy"),
        "auto_merge_ready": len(dedup_winners),
        "unresolved_targets": sum(1 for c in dedup_winners if str(c["target_path_suggestion"]).startswith("UNRESOLVED")),
    }
    if run_meta:
        meta_payload.update(run_meta)

    payload = {
        "projects": mapped,
        "capability_winners": winners_by_cap,
        "all_decisions": decisions,
        "merge_candidates": dedup_winners,
        "suppressed_duplicates": dedup_suppressed,
        "manual_review_candidates": manual_review_pool,
        "merge_waves": waves,
        "structure_preparation": {"missing_directories": missing_dirs},
        "meta": meta_payload,
    }

    def _inject_source_scoped_paths(node):
        if isinstance(node, dict):
            source_project = node.get("source_project")
            source_path = node.get("source_path")
            if source_project and source_path and not node.get("source_scoped_path"):
                node["source_scoped_path"] = f"{source_project}::{source_path}"
            for value in node.values():
                _inject_source_scoped_paths(value)
        elif isinstance(node, list):
            for item in node:
                _inject_source_scoped_paths(item)

    _inject_source_scoped_paths(payload)

    ensure_valid_payload("fractal_map", payload)

    json_path = RAW_DIR / "fractal_map.json"
    md_path = REPORTS_DIR / "fractal_map.md"
    save_fractal_map_data(payload)

    # Keep sidecar snapshots aligned when they exist.
    for sidecar_name in ("fractal_map.atlas.json", "fractal_map.content.json"):
        sidecar_path = RAW_DIR / sidecar_name
        if not sidecar_path.exists():
            continue
        try:
            sidecar_payload = load_json_file(sidecar_path, {})
            _inject_source_scoped_paths(sidecar_payload)
            save_json_atomic(sidecar_path, sidecar_payload)
        except Exception as exc:
            logger.warning(f"[PROFILE] Could not normalize source_scoped_path in {sidecar_name}: {exc}")

    lines = [
        "# Fractal Architecture Map v16.3",
        "",
        "Deterministic studio mapping + main-first auto winner + export recommendation + duplicate suppression.",
        "",
        "## Capability Winners",
        "| Capability | Winner | Runner-up |",
        "|---|---|---|",
    ]
    for cap, data in winners_by_cap.items():
        r = data.get("ranking", [])
        w = r[0]["project"] if len(r) > 0 else "-"
        ru = r[1]["project"] if len(r) > 1 else "-"
        lines.append(f"| `{cap}` | {w} | {ru} |")

    lines += ["", "## Main-First Decisions (Top)", "| Name | Action | Chosen | Delta | Bucket |", "|---|---|---|---:|---|"]
    for d in decisions[:120]:
        lines.append(f"| `{d.get('display_name') or d['name']}` | {d['action']} | {d['chosen_source']} | {d['delta']} | `{d['main_first_bucket']}` |")

    lines += ["", "## Merge Candidates (Deduplicated)", "| Name | Source | Studio->Layer | Risk | Conf | Delta | Export | Target | Gateway |", "|---|---|---|---|---:|---:|---|---|---|"]
    for c in dedup_winners[:260]:
        sl = f"{c['target_studio']}::{c['target_layer']}"
        source = c.get("source_scoped_path") or f"{c.get('source_project', 'UNKNOWN')}::{c.get('source_path', '')}"
        lines.append(f"| `{c.get('display_name') or c['name']}` | `{source}` | `{sl}` | {c['risk']} | {c['confidence']} | {c['delta']} | {c['export_recommendation']} | `{c['target_path_suggestion']}` | `{c['gateway_entry']}` |")

    lines += ["", "## Suppressed Duplicates", "| Name | Suppressed By | Group |", "|---|---|---|"]
    for d in dedup_suppressed[:180]:
        lines.append(f"| `{d.get('display_name') or d['name']}` | `{d.get('display_suppressed_by') or d['suppressed_by']}` | `{d['duplicate_group_id']}` |")

    lines += ["", "## Manual Review Queue", "| Name | Source | Studio->Layer | Risk | Conf | Review Bucket | Reasons | Target |", "|---|---|---|---|---:|---|---|---|"]
    for c in manual_review_pool[:180]:
        sl = f"{c['target_studio']}::{c['target_layer']}"
        reasons = ", ".join(c.get("review_reasons", []))
        source = c.get("source_scoped_path") or f"{c.get('source_project', 'UNKNOWN')}::{c.get('source_path', '')}"
        lines.append(f"| `{c.get('display_name') or c['name']}` | `{source}` | `{sl}` | {c['risk']} | {c['confidence']} | `{c.get('review_bucket', 'target_contract_weak')}` | `{reasons}` | `{c['target_path_suggestion']}` |")

    lines += ["", "## Merge Candidates By Source Project"]
    grouped_candidates = defaultdict(list)
    for c in dedup_winners:
        grouped_candidates[str(c.get("source_project") or "UNKNOWN")].append(c)
    for project, items in sorted(grouped_candidates.items(), key=lambda item: len(item[1]), reverse=True):
        lines += ["", f"### {project} ({len(items)})", "| Name | Studio->Layer | Risk | Conf | Delta | Target |", "|---|---|---|---:|---:|---|"]
        for c in items[:40]:
            sl = f"{c['target_studio']}::{c['target_layer']}"
            lines.append(
                f"| `{c.get('display_name') or c['name']}` | `{sl}` | {c['risk']} | {c['confidence']} | {c['delta']} | `{c['target_path_suggestion']}` |"
            )
        if len(items) > 40:
            lines.append(f"| ... | *and {len(items) - 40} more* | | | | |")

    lines += ["", "## Manual Review By Source Project"]
    grouped_review = defaultdict(list)
    for c in manual_review_pool:
        grouped_review[str(c.get("source_project") or "UNKNOWN")].append(c)
    for project, items in sorted(grouped_review.items(), key=lambda item: len(item[1]), reverse=True):
        lines += ["", f"### {project} ({len(items)})", "| Name | Studio->Layer | Risk | Conf | Review Bucket | Target |", "|---|---|---|---:|---|---|"]
        for c in items[:30]:
            sl = f"{c['target_studio']}::{c['target_layer']}"
            lines.append(
                f"| `{c.get('display_name') or c['name']}` | `{sl}` | {c['risk']} | {c['confidence']} | `{c.get('review_bucket', 'target_contract_weak')}` | `{c['target_path_suggestion']}` |"
            )
        if len(items) > 30:
            lines.append(f"| ... | *and {len(items) - 30} more* | | | | |")

    lines += ["", "## Structure Preparation", *(f"- `{d}`" for d in missing_dirs[:100]), "", "## Doctrine Gate", "- `python audit_v5.py` after each merge batch."]
    save_text_atomic(md_path, "\n".join(lines))
    return json_path, md_path, payload


def load_previous_mapped_data() -> Dict[str, Dict]:
    global GLOBAL_FRACTAL_CACHE
    if GLOBAL_FRACTAL_CACHE:
        return GLOBAL_FRACTAL_CACHE
        
    try:
        logger.info("[GLOBAL] Loading shared fractal metadata...")
        data = load_fractal_map_data()
        res = data.get("projects", {})
        GLOBAL_FRACTAL_CACHE.update(res)
        return res
    except Exception as e:
        logger.warning(f"Could not load previous fractal map for lifting: {e}")
        return {}


def run_fractal_mapper(stale_projects=None, changed_files=None):
    total_start = perf_counter()
    root = ROOT
    setup_start = perf_counter()
    projects = resolve_runtime_projects(root)
    requested_semantic_mode = os.getenv("CODEMAPS_FRACTAL_SEMANTIC_MODE", "atlas").strip().lower()
    if requested_semantic_mode not in {"atlas", "content"}:
        requested_semantic_mode = "atlas"
    genome_raw = load_genome_data()
    surgical_raw = load_json_file(RAW_DIR / "surgical_discovery.json", [])
    genome = normalize_genome(genome_raw)
    coupling = normalize_coupling(surgical_raw)
    atlas_file_map: Dict[str, Dict[str, Dict]] = {}
    try:
        atlas_raw = load_atlas_data()
        for project_key, pdata in atlas_raw.items():
            if project_key == "symbols" or not isinstance(pdata, dict):
                continue
            normalized = normalize_project_name(str(project_key))
            files_payload = pdata.get("files", {})
            if isinstance(files_payload, dict):
                atlas_file_map[normalized] = files_payload
    except Exception as exc:
        logger.warning(f"[PROFILE] Could not preload atlas file map for semantic lift: {exc}")
    setup_elapsed = perf_counter() - setup_start
    
    prev_mapped = {}
    # [Phase 6] Surgical Optimization: Always load cache if we are in surgical mode
    if stale_projects is not None or changed_files is not None:
        prev_mapped = load_previous_mapped_data()

    mapped = {}
    semantic_usage = {"atlas": 0, "content": 0}
    project_metrics = []
    build_jobs = []
    for k, v in projects.items():
        project_start = perf_counter()
        pkey = normalize_project_name(k)
        
        # [Phase 6] Decision Logic: 
        # 1. If stale_projects is defined, check if this project is stale.
        # 2. If changed_files is defined, check if this project contains any of them.
        is_stale = stale_projects is not None and pkey in stale_projects
        has_change = False
        if changed_files:
            # Check if any changed file starts with this project's path
            project_path_str = str(v.resolve()).lower()
            has_change = any(str(Path(f).resolve()).lower().startswith(project_path_str) for f in changed_files)

        if not is_stale and not has_change:
            if prev_mapped and pkey in prev_mapped:
                logger.info(f"[DNA] [LIFT] Fractal Map for {pkey} lifted from cache.")
                mapped[k] = prev_mapped[pkey]
                project_elapsed = perf_counter() - project_start
                project_metrics.append({"project": pkey, "phase": "lift", "total_s": round(project_elapsed, 3)})
                logger.info(f"[PROFILE] Fractal {pkey} | phase=lift total={project_elapsed:.2f}s")
            else:
                # [FIX] In surgical mode, if a project isn't stale/changed AND not filtered, skip it entirely.
                if stale_projects is not None:
                    logger.info(f"[DNA] [SKIP] Project {pkey} ignored in surgical run.")
                    continue
                # If no filter (full run), we must build it
                logger.info(f"[ATLAS] Building fractal map for {k} (Full Run)...")
                build_jobs.append((k, v, pkey, project_start))
        else:
            logger.info(f"[ATLAS] Building fractal map for {k}...")
            build_jobs.append((k, v, pkey, project_start))

    def build_project(job):
        project_key, project_path, normalized_key, started_at = job
        build_started_at = perf_counter()
        result = map_project(
            project_path,
            normalized_key,
            genome,
            prev_mapped.get(normalized_key),
            changed_files,
            atlas_file_map.get(normalized_key, {}),
        )
        elapsed = perf_counter() - build_started_at
        return project_key, normalized_key, result, elapsed

    if build_jobs:
        env_value = os.getenv("CODEMAPS_FRACTAL_WORKERS", "").strip()
        env_workers = int(env_value) if env_value.isdigit() and int(env_value) > 0 else None
        default_workers = min(4, max(1, os.cpu_count() or 2), len(build_jobs))
        max_workers = min(env_workers, len(build_jobs)) if env_workers else default_workers
        unsafe_override = os.getenv("CODEMAPS_ALLOW_UNSAFE_FRACTAL_PARALLEL", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        safe_cap = min(4, len(build_jobs))
        if max_workers > safe_cap and not unsafe_override:
            logger.warning(
                "[PROFILE] Fractal workers=%s exceeds safe cap=%s; clamping. "
                "Set CODEMAPS_ALLOW_UNSAFE_FRACTAL_PARALLEL=1 to bypass.",
                max_workers,
                safe_cap,
            )
            max_workers = safe_cap
        logger.info(f"[PROFILE] Fractal project workers: {max_workers}")
        if max_workers <= 1:
            build_results = [build_project(job) for job in build_jobs]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                build_results = list(executor.map(build_project, build_jobs))

        for project_key, normalized_key, result, elapsed in build_results:
            mapped[project_key] = result
            project_metrics.append({"project": normalized_key, "phase": "build", "total_s": round(elapsed, 3)})
            profile = result.get("_profile", {})
            semantic_usage["atlas"] += int(profile.get("semantic_from_atlas", 0) or 0)
            semantic_usage["content"] += int(profile.get("semantic_from_content", 0) or 0)
            logger.info(
                "[PROFILE] Fractal %s | phase=build lifted=%s scanned=%s loop=%.2fs total=%.2fs"
                % (
                    normalized_key,
                    profile.get("lifted_files", 0),
                    profile.get("scanned_files", 0),
                    float(profile.get("loop_s", 0.0)),
                    elapsed,
                )
            )

    analysis_start = perf_counter()
    sem_idx = build_semantic_index(mapped)
    capability_start = perf_counter()
    winners_by_cap = build_capability_winners(genome, sem_idx, coupling)
    capability_elapsed = perf_counter() - capability_start
    decisions_start = perf_counter()
    decisions, pool, manual_review_pool = build_decisions(genome, sem_idx, coupling)
    dedup_winners, dedup_suppressed = deduplicate_candidates(pool)
    waves = build_waves(dedup_winners, manual_review_pool)
    missing_dirs = prepare_missing_dirs(root, waves)
    decisions_elapsed = perf_counter() - decisions_start
    analysis_elapsed = perf_counter() - analysis_start

    write_start = perf_counter()
    run_meta = {
        "semantic_source_mode_requested": requested_semantic_mode,
        "semantic_scanned_from_atlas": semantic_usage["atlas"],
        "semantic_scanned_from_content": semantic_usage["content"],
    }
    j, m, payload = write_outputs(
        root,
        mapped,
        winners_by_cap,
        decisions,
        dedup_winners,
        dedup_suppressed,
        manual_review_pool,
        waves,
        missing_dirs,
        run_meta=run_meta,
    )
    write_current_atlas_lineage(
        artifact_id="fractal_map",
        producer="tools.engines.fractal_mapper",
        artifact_payload=payload,
        atlas=atlas_raw,
        dependency_payloads={"genome": genome_raw, "surgical_discovery": surgical_raw},
    )
    write_elapsed = perf_counter() - write_start
    total_elapsed = perf_counter() - total_start
    logger.info(
        "[PROFILE] Fractal total | projects=%s setup=%.2fs analysis=%.2fs write=%.2fs total=%.2fs"
        % (len(project_metrics), setup_elapsed, analysis_elapsed, write_elapsed, total_elapsed)
    )
    logger.info(
        "[PROFILE] Fractal analysis breakdown | capability=%.2fs decisions=%.2fs"
        % (capability_elapsed, decisions_elapsed)
    )
    return True

def main():
    import sys
    stale_arg = next((arg for arg in sys.argv if arg.startswith("--stale-projects=")), None)
    changed_arg = next((arg for arg in sys.argv if arg.startswith("--changed-files=")), None)
    
    stale_projects = None
    if stale_arg:
        stale_projects = stale_arg.split("=")[1].split(",")

    changed_files = None
    if changed_arg:
        changed_files = changed_arg.split("=")[1].split(",")

    run_fractal_mapper(stale_projects, changed_files)


if __name__ == "__main__":
    main()
