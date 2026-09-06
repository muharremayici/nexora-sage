"""
Dead Code Detector - finds exported symbols that are never imported anywhere.
Reads the Atlas payload to cross-reference all exports vs all imports.
"""
import json
import os
import re
import fnmatch
import hashlib
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from time import perf_counter

from tools.core.artifact_validator import ensure_valid_payload
from tools.core.atlas_io import load_atlas_data
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.config import ROOT, RAW_DIR, REPORTS_DIR, CONFIG_DIR, save_json_atomic, save_text_atomic, DOCTRINE
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.core.engine_progress import EngineProgress
from tools.core.dead_code_allowlist_policy import get_allowlist_scope, scope_allows_global, scope_allows_project
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.path_engine import to_posix_path
from tools.core.projects_registry import project_display_name
from tools.core.projects_registry import resolve_runtime_projects
from tools.core.runtime_project_scope import canonical_project_keys, project_runtime_atlas
from tools.core.source_snapshot_reader import load_source_text
from tools.core.source_files import is_analysis_source_file


ALLOWLIST_FILENAME = "dead_code_intent_allowlist.json"
DEAD_CODE_CACHE_FILENAME = ".dead_code_project_cache.json"
DEAD_CODE_CACHE_VERSION = "dead_code_project_cache_v7"
DEAD_CODE_ENGINE_SIGNATURE = "dead_code_logic_v33_atlas_public_contracts"
VALID_SYMBOL_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
DYNAMIC_NAMED_IMPORT_RE = re.compile(
    r"(?:const|let|var)\s*{(?P<names>[^}]+)}\s*=\s*(?:await\s+)?import\(\s*['\"](?P<src>[^'\"]+)['\"]\s*\)",
    re.MULTILINE,
)
DYNAMIC_THEN_NAMED_IMPORT_RE = re.compile(
    r"import\(\s*['\"](?P<src>[^'\"]+)['\"]\s*\)\s*\.then\s*\(\s*(?:\(\s*)?{(?P<names>[^}]+)}",
    re.MULTILINE,
)


class SizeBoundedDict(dict):
    def __init__(self, max_size=500, *args, **kwargs):
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


def _deterministic_hash(val: tuple) -> int:
    h = hashlib.md5(str(val).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


class DeadCodeDetector:
    def __init__(self, cache_path: Path | None = None):
        # Strict Schema Validation for DOCTRINE keys (Fail-Fast)
        heuristics = require_doctrine_mapping("dead_code_heuristics")
        if not heuristics:
            raise ValueError("[DOCTRINE] Missing mandatory section 'dead_code_heuristics' in architecture_doctrine.json!")
            
        required_keys = [
            "dynamic_import_resolutions",
            "barrel_public_surfaces",
            "nest_runtime_decorators",
            "test_support_heuristics",
            "reference_example_heuristics",
            "e2e_page_object_heuristics",
            "generated_surfaces",
            "dynamic_registry_heuristics",
            "framework_discovered_exports"
        ]
        for key in required_keys:
            if key not in heuristics:
                raise ValueError(f"[DOCTRINE] Schema validation failed: 'dead_code_heuristics.{key}' is missing in architecture_doctrine.json!")

        self.projects = resolve_runtime_projects(ROOT)
        self._local_usage_cache = SizeBoundedDict(max_size=1000)
        self._local_dependency_refs_cache = defaultdict(set)
        self._file_content_cache = SizeBoundedDict(max_size=500)
        self._identifier_frequency_cache = SizeBoundedDict(max_size=1000)
        self._compatibility_file_cache = SizeBoundedDict(max_size=1000)
        self._legacy_contract_cache = SizeBoundedDict(max_size=1000)
        self._contract_surface_cache = SizeBoundedDict(max_size=1000)
        self._constant_surface_cache = SizeBoundedDict(max_size=1000)
        self._type_surface_cache = SizeBoundedDict(max_size=1000)
        self._type_only_export_cache = SizeBoundedDict(max_size=1000)
        self._generated_symbol_reference_cache = SizeBoundedDict(max_size=1000)
        self._generated_contract_manifest_cache = SizeBoundedDict(max_size=1000)
        self._runtime_consumed_contract_cache = SizeBoundedDict(max_size=1000)
        self._ast_runtime_contract_cache = SizeBoundedDict(max_size=1000)
        self._ast_graphql_contract_cache = SizeBoundedDict(max_size=1000)
        self._package_public_export_patterns_cache = SizeBoundedDict(max_size=1000)
        self._dynamic_registry_usage_cache = SizeBoundedDict(max_size=1000)
        self._package_scan_stats = {}
        self._contract_registry_rules = self._load_contract_registry_rules()
        self._allowlist_scope = get_allowlist_scope(CONFIG_DIR)
        self._allowlist_rules = self._load_intent_allowlist_rules()
        self._project_cache_path = cache_path or (RAW_DIR / DEAD_CODE_CACHE_FILENAME)
        self._project_cache = self._load_project_cache()
        self._project_cache_dirty = False

    def _load_contract_registry_rules(self) -> list[dict]:
        """
        Doctrine-driven registry for contract-only/export surfaces that should not
        be emitted as dead code across React ecosystem repos.
        """
        raw_rules = require_doctrine_mapping("dead_code_heuristics").get("contract_surface_registry")
        normalized: list[dict] = []
        for idx, raw in enumerate(raw_rules):
            if not isinstance(raw, dict):
                continue
            if raw.get("enabled", True) is False:
                continue
            normalized.append(
                {
                    "id": str(raw.get("id") or f"registry_rule_{idx+1}"),
                    "reason": str(raw.get("reason") or "contract_registry_surface"),
                    "label": str(raw.get("label") or ""),
                    "path_globs": [to_posix_path(str(p)) for p in (raw.get("path_globs") or []) if str(p).strip()],
                    "file_globs": [str(p) for p in (raw.get("file_globs") or []) if str(p).strip()],
                    "path_tokens": [to_posix_path(str(p)).lower() for p in (raw.get("path_tokens") or []) if str(p).strip()],
                    "symbol_prefixes": [str(p) for p in (raw.get("symbol_prefixes") or []) if str(p).strip()],
                    "symbol_suffixes": [str(p) for p in (raw.get("symbol_suffixes") or []) if str(p).strip()],
                    "symbol_regex": str(raw.get("symbol_regex") or "").strip(),
                    "content_markers": [str(p).lower() for p in (raw.get("content_markers") or []) if str(p).strip()],
                }
            )
        return normalized

    @staticmethod
    def _match_field(actual: str, expected: str, *, path_mode: bool = False) -> bool:
        if expected is None:
            return True
        expected_value = str(expected)
        if expected_value.strip() == "*":
            return True
        actual_value = str(actual or "")
        if path_mode:
            expected_value = to_posix_path(expected_value)
            actual_value = to_posix_path(actual_value)
        if expected_value.startswith("re:"):
            try:
                return re.search(expected_value[3:], actual_value) is not None
            except re.error:
                return False
        if any(ch in expected_value for ch in "*?[]"):
            return fnmatch.fnmatchcase(actual_value, expected_value)
        return actual_value == expected_value

    def _load_intent_allowlist_rules(self) -> list[dict]:
        rules = []
        global_allowlist = CONFIG_DIR / "golden" / ALLOWLIST_FILENAME
        payloads = []
        if scope_allows_global(self._allowlist_scope) and global_allowlist.exists():
            try:
                payloads.append(("global", json.loads(global_allowlist.read_text(encoding="utf-8"))))
            except Exception as exc:
                logger.warning("Failed to load global allowlist rules from %s: %s", global_allowlist, exc)
                payloads.append(("global", {}))

        projects_dir = CONFIG_DIR / "golden" / "projects"
        if scope_allows_project(self._allowlist_scope) and projects_dir.exists():
            for project_dir in sorted(projects_dir.iterdir(), key=lambda p: p.name):
                if not project_dir.is_dir():
                    continue
                candidate = project_dir / ALLOWLIST_FILENAME
                if not candidate.exists():
                    continue
                try:
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning("Failed to load project allowlist rules from %s: %s", candidate, exc)
                    payload = {}
                payloads.append((f"project:{project_dir.name}", payload))

        for scope, payload in payloads:
            for rule in payload.get("rules", []) if isinstance(payload, dict) else []:
                if not isinstance(rule, dict):
                    continue
                if rule.get("enabled", True) is False:
                    continue
                matcher = rule.get("match", {})
                if not isinstance(matcher, dict) or not matcher:
                    continue
                rules.append(
                    {
                        "scope": scope,
                        "id": str(rule.get("id") or f"{scope}_rule_{len(rules)+1}"),
                        "match": matcher,
                        "label": str(rule.get("label") or ""),
                    }
                )
        return rules

    def _allowlist_match(self, item: dict) -> dict | None:
        if not self._allowlist_rules:
            return None
        path_fields = {"file", "scoped_file"}
        for rule in self._allowlist_rules:
            matcher = rule.get("match", {})
            passed = True
            for key, expected in matcher.items():
                if not self._match_field(
                    str(item.get(key) or ""),
                    str(expected),
                    path_mode=key in path_fields,
                ):
                    passed = False
                    break
            if passed:
                return {"rule_id": rule["id"], "scope": rule["scope"], "label": rule.get("label", "")}
        return None

    @staticmethod
    def _stable_fingerprint(data) -> str:
        try:
            serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return hashlib.sha1(serialized.encode("utf-8")).hexdigest()
        except Exception:
            return ""

    def _doctrine_signature(self) -> str:
        return self._stable_fingerprint(
            {
                "dead_code_heuristics": require_doctrine_mapping("dead_code_heuristics"),
                "analysis_symbol_significance": require_doctrine_mapping("analysis_heuristics").get("symbol_significance"),
                "intelligence_tuning": require_doctrine_mapping("intelligence_tuning").get("confidence_tiers"),
            }
        )

    def _allowlist_signature(self) -> str:
        return self._stable_fingerprint(
            {
                "scope": self._allowlist_scope,
                "rules": self._allowlist_rules,
            }
        )

    def _project_atlas_signature(self, project_data: dict) -> str:
        files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
        normalized = []
        for rel_path, file_data in sorted(files.items()):
            if not isinstance(file_data, dict):
                continue
            rel_norm = to_posix_path(rel_path)
            if not is_analysis_source_file(rel_norm):
                continue
            normalized.append(
                {
                    "file": rel_norm,
                    "ast_contract_version": file_data.get("ast_contract_version"),
                    "mtime": file_data.get("mtime"),
                    "hash": file_data.get("hash"),
                    "size": file_data.get("size"),
                    "fingerprint": file_data.get("fingerprint"),
                    "symbol_count": len(file_data.get("symbols", [])),
                    "export_count": len(file_data.get("exports", [])),
                    "import_record_count": len(file_data.get("import_records", [])),
                }
            )
        return self._stable_fingerprint(normalized)

    def _load_project_cache(self) -> dict:
        cache_path = self._project_cache_path
        if not cache_path.exists():
            return {"meta": {"version": DEAD_CODE_CACHE_VERSION}, "projects": {}}
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load project cache from %s: %s", cache_path, exc)
            return {"meta": {"version": DEAD_CODE_CACHE_VERSION}, "projects": {}}
        if not isinstance(payload, dict):
            return {"meta": {"version": DEAD_CODE_CACHE_VERSION}, "projects": {}}
        if str(payload.get("meta", {}).get("version", "")) != DEAD_CODE_CACHE_VERSION:
            return {"meta": {"version": DEAD_CODE_CACHE_VERSION}, "projects": {}}
        projects = payload.get("projects", {})
        if not isinstance(projects, dict):
            projects = {}
        return {"meta": {"version": DEAD_CODE_CACHE_VERSION}, "projects": projects}

    def _persist_project_cache(self):
        if not self._project_cache_dirty:
            return
        cache_path = self._project_cache_path
        save_json_atomic(cache_path, self._project_cache)
        self._project_cache_dirty = False

    def _read_project_file(self, project: str, rel_path: str) -> str | None:
        cache_key = (project, rel_path)
        if cache_key in self._file_content_cache:
            return self._file_content_cache[cache_key]

        project_root = self.projects.get(project)
        if not project_root:
            self._file_content_cache[cache_key] = None
            return None

        source_path = project_root / rel_path
        content = load_source_text(
            project,
            rel_path,
            fallback_path=source_path,
            component="dead_code_detector",
        )
        self._file_content_cache[cache_key] = content
        return content

    @staticmethod
    def _scoped_file(project: str, rel_path: str) -> str:
        return f"{project}::{to_posix_path(rel_path)}"

    def _project_specificity_score(self, project: str) -> tuple[int, int, int]:
        """
        Prefer more specific project roots for overlapping files.
        Example: APPS (root=.../apps) should win over MAIN (root=.../).
        """
        root = self.projects.get(project)
        if not root:
            return (0, 0, 0)
        try:
            resolved = Path(root).resolve()
            depth = len(resolved.parts)
            length = len(str(resolved))
        except Exception as exc:
            logger.warning("Failed to resolve project root path %s: %s", root, exc)
            depth = 0
            length = 0
        # Non-MAIN wins ties over MAIN.
        non_main = 0 if str(project).upper() == "MAIN" else 1
        return (depth, non_main, length)

    def _physical_origin_key(self, item: dict) -> tuple[str, str]:
        """
        Stable de-dup key based on physical file path + symbol.
        Falls back to project-scoped key when path cannot be resolved.
        """
        project = str(item.get("project") or "")
        rel_file = to_posix_path(str(item.get("file") or ""))
        symbol = str(item.get("symbol") or "")
        root = self.projects.get(project)
        if root and rel_file:
            try:
                physical = str((Path(root) / rel_file).resolve())
                return (physical, symbol)
            except Exception as exc:
                logger.warning("Failed to resolve physical origin path for %s / %s: %s", root, rel_file, exc)
        return (f"{project}::{rel_file}", symbol)

    def _deduplicate_by_physical_origin(self, dead_items: list[dict]) -> tuple[list[dict], int]:
        """
        Remove duplicate dead-code entries that point to the same physical file+symbol
        through overlapping project roots (e.g., MAIN + companion).
        """
        confidence_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        selected: dict[tuple[str, str], dict] = {}
        selected_rank: dict[tuple[str, str], tuple[tuple[int, int, int], int, str]] = {}

        for item in dead_items:
            key = self._physical_origin_key(item)
            project = str(item.get("project") or "")
            specificity = self._project_specificity_score(project)
            confidence = str(item.get("confidence") or "LOW").upper()
            confidence_priority = -confidence_rank.get(confidence, 9)
            ranking = (specificity, confidence_priority, project)

            existing_rank = selected_rank.get(key)
            if existing_rank is None or ranking > existing_rank:
                selected[key] = item
                selected_rank[key] = ranking

        deduped = list(selected.values())
        removed = max(0, len(dead_items) - len(deduped))
        return deduped, removed

    @staticmethod
    def _canonical_symbol_name(symbol: str) -> str:
        clean = (symbol or "").strip()
        sig_config = require_doctrine_mapping("analysis_heuristics").get("symbol_significance")
        prefixes = sig_config.get("canonical_prefixes", ["type", "interface", "enum", "class", "function", "const", "let", "var"])
        
        for prefix in prefixes:
            if clean.startswith(f"{prefix} "):
                clean = clean[len(prefix):].strip()
        if not VALID_SYMBOL_RE.fullmatch(clean):
            return ""
        return clean

    @staticmethod
    def _parse_dynamic_named_imports(content: str) -> list[tuple[str, list[str]]]:
        """
        Capture named symbols consumed through dynamic import destructuring:
        const { exportedName } = await import("./module")
        import("./module").then(({ exportedName }) => ...)
        """
        parsed: list[tuple[str, list[str]]] = []
        if not content:
            return parsed

        for pattern in (DYNAMIC_NAMED_IMPORT_RE, DYNAMIC_THEN_NAMED_IMPORT_RE):
            for match in pattern.finditer(content):
                src = str(match.group("src") or "").strip()
                names_blob = str(match.group("names") or "")
                names: list[str] = []
                for raw_name in names_blob.split(","):
                    token = raw_name.strip()
                    if not token:
                        continue
                    exported_name = token.split(":", 1)[0].strip()
                    exported_name = exported_name.split("=", 1)[0].strip()
                    canonical = DeadCodeDetector._canonical_symbol_name(exported_name)
                    if canonical:
                        names.append(canonical)
                if src and names:
                    parsed.append((src, names))
        return parsed

    @staticmethod
    def _resolve_dynamic_import_target(current_path: str, source: str, available_files: set[str]) -> str:
        source_norm = to_posix_path(source)
        if not source_norm.startswith("."):
            return ""

        current_parent = PurePosixPath(to_posix_path(current_path)).parent
        base = PurePosixPath(to_posix_path(str(current_parent / source_norm)))
        heuristics = require_doctrine_mapping("dead_code_heuristics")
        import_config = heuristics.get("dynamic_import_resolutions", {})
        extensions = import_config.get("extensions", [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"])
        index_files = import_config.get("index_files", ["index.ts", "index.tsx", "index.js", "index.jsx"])
        
        candidates = [str(base)]
        for ext in extensions:
            candidates.append(f"{base}{ext}")
        for idx in index_files:
            candidates.append(f"{base}/{idx}")
        for candidate in candidates:
            normalized = to_posix_path(candidate)
            if normalized in available_files:
                return normalized
        return ""

    def _get_identifier_frequency(self, project: str, rel_path: str) -> Counter:
        cache_key = (project, rel_path)
        if cache_key in self._identifier_frequency_cache:
            return self._identifier_frequency_cache[cache_key]

        content = self._read_project_file(project, rel_path)
        if not content:
            freq = Counter()
            self._identifier_frequency_cache[cache_key] = freq
            return freq

        tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", content)
        freq = Counter(tokens)
        self._identifier_frequency_cache[cache_key] = freq
        return freq

    def _has_local_symbol_usage(self, project: str, rel_path: str, symbol: str) -> bool:
        canonical = self._canonical_symbol_name(symbol)
        if not canonical:
            return False

        cache_key = (project, rel_path, canonical)
        if cache_key in self._local_usage_cache:
            return self._local_usage_cache[cache_key]

        # Fast path: Atlas-local symbol dependencies.
        if canonical in self._local_dependency_refs_cache.get((project, rel_path), set()):
            self._local_usage_cache[cache_key] = True
            return True

        # Precision fallback: token-frequency check protects against parser edge-cases.
        occurrences = self._get_identifier_frequency(project, rel_path).get(canonical, 0)
        is_locally_used = occurrences > 1
        self._local_usage_cache[cache_key] = is_locally_used
        return is_locally_used

    def _is_compatibility_bridge_file(self, project: str, rel_path: str) -> bool:
        cache_key = (project, rel_path)
        if cache_key in self._compatibility_file_cache:
            return self._compatibility_file_cache[cache_key]

        rel_norm = to_posix_path(rel_path).lower()
        markers_config = require_doctrine_mapping("dead_code_heuristics").get("exclusion_markers")
        path_tokens = markers_config.get("path_tokens", [])
        content_markers = markers_config.get("content_markers", [])

        if not any(token in rel_norm for token in path_tokens):
            self._compatibility_file_cache[cache_key] = False
            return False

        content = self._read_project_file(project, rel_path)
        if content is None:
            self._compatibility_file_cache[cache_key] = False
            return False

        lowered = content.lower()
        is_bridge = any(marker in lowered for marker in content_markers)
        self._compatibility_file_cache[cache_key] = is_bridge
        return is_bridge

    def _is_barrel_public_surface(self, rel_path: str) -> bool:
        rel_norm = to_posix_path(rel_path).lower()
        file_name = rel_norm.rsplit("/", 1)[-1]
        heuristics = require_doctrine_mapping("dead_code_heuristics")
        barrel_config = heuristics.get("barrel_public_surfaces", {})
        file_names = barrel_config.get("file_names", ["index.ts", "index.tsx", "exports.ts", "exports.tsx"])
        file_prefixes = barrel_config.get("file_prefixes", ["public."])
        file_suffixes = barrel_config.get("file_suffixes", [".ts", ".tsx", ".js", ".jsx"])
        
        if file_name in file_names:
            return True
        if any(file_name.startswith(p) for p in file_prefixes) and any(file_name.endswith(s) for s in file_suffixes):
            return True
        return False

    def _is_nest_runtime_export(self, project: str, rel_path: str, symbol: str) -> bool:
        """
        NestJS decorators often mark classes consumed by framework runtime/reflection,
        not always by explicit import edges.
        """
        canonical = self._canonical_symbol_name(symbol)
        if not canonical:
            return False

        content = self._read_project_file(project, rel_path)
        if not content:
            return False

        heuristics = require_doctrine_mapping("dead_code_heuristics")
        markers = heuristics.get("nest_runtime_decorators", [
            "@Module(", "@Injectable(", "@Controller(", "@Resolver(",
            "@InputType(", "@ObjectType(", "@ArgsType(", "@Schema(",
            "@Processor(", "@Gateway("
        ])
        if not any(marker in content for marker in markers):
            return False

        class_decl = re.search(
            rf"export\s+class\s+{re.escape(canonical)}\b",
            content,
            flags=re.MULTILINE,
        )
        if not class_decl:
            return False

        return True

    def _package_public_export_patterns(self, project: str, project_data: dict | None = None) -> list[str]:
        cached = self._package_public_export_patterns_cache.get(project)
        if cached is not None:
            return cached
        contracts = project_data.get("public_contracts", {}) if isinstance(project_data, dict) else {}
        raw_patterns = contracts.get("entry_patterns", []) if isinstance(contracts, dict) else []
        deduped = sorted({to_posix_path(str(pattern)) for pattern in raw_patterns if str(pattern).strip()})
        self._package_scan_stats[project] = {
            "source": "atlas_public_contracts",
            "package_files": len(contracts.get("packages", [])) if isinstance(contracts.get("packages"), list) else 0,
            "entry_patterns": len(deduped),
        }
        self._package_public_export_patterns_cache[project] = deduped
        return deduped

    @staticmethod
    def _matches_package_public_export_surface(rel_path: str, patterns: list[str]) -> bool:
        rel_norm = to_posix_path(rel_path)
        if not rel_norm:
            return False
        for pattern in patterns:
            if fnmatch.fnmatchcase(rel_norm, pattern):
                return True
        return False

    def _is_package_public_export_surface(self, project: str, rel_path: str, project_data: dict | None = None) -> bool:
        return self._matches_package_public_export_surface(
            rel_path,
            self._package_public_export_patterns(project, project_data),
        )

    def _public_export_chain_surfaces(
        self,
        project: str,
        files: dict,
        public_contracts: dict | None = None,
    ) -> tuple[set[str], set[tuple[str, str]]]:
        """
        Resolve files/symbols exported through package public entrypoints.

        Internal imports are not required here: package metadata is the public
        contract. If a public entrypoint re-exports a deep file through barrel
        chains, the downstream export remains externally active even when no
        source file inside the repo imports it.
        """
        if not isinstance(files, dict):
            return set(), set()

        available_files = {to_posix_path(path) for path in files.keys()}
        edges: dict[str, list[tuple[str, set[str] | None]]] = defaultdict(list)
        public_entries: set[str] = set()
        package_patterns = sorted({str(pattern) for pattern in (public_contracts or {}).get("entry_patterns", [])})

        for rel_path, f_data in files.items():
            n_curr = to_posix_path(rel_path)
            if not is_analysis_source_file(n_curr):
                continue
            if self._matches_package_public_export_surface(n_curr, package_patterns):
                public_entries.add(n_curr)

            for sym_info in f_data.get("exports", []) if isinstance(f_data, dict) else []:
                if not isinstance(sym_info, dict):
                    continue
                module_specifier = str(sym_info.get("moduleSpecifier") or "")
                if not module_specifier:
                    continue
                target = self._resolve_dynamic_import_target(n_curr, module_specifier, available_files)
                if not target:
                    continue

                export_type = str(sym_info.get("type") or "")
                if export_type == "ProxyExport":
                    exported_names = {
                        self._canonical_symbol_name(name)
                        for name in (sym_info.get("exportedNames") or [])
                        if self._canonical_symbol_name(name)
                    }
                    edges[n_curr].append((target, exported_names or None))
                    continue

                if export_type == "ReExportedSymbol":
                    raw_names = list(sym_info.get("dependencies") or []) or list(sym_info.get("exportedNames") or [])
                    names = {self._canonical_symbol_name(name) for name in raw_names if self._canonical_symbol_name(name)}
                    if names:
                        edges[n_curr].append((target, names))

        public_files: set[str] = set()
        public_symbols: set[tuple[str, str]] = set()
        queue: list[tuple[str, frozenset[str] | None]] = [(entry, None) for entry in sorted(public_entries)]
        seen: set[tuple[str, frozenset[str] | None]] = set()

        while queue:
            current, requested_names = queue.pop(0)
            state = (current, requested_names)
            if state in seen:
                continue
            seen.add(state)

            for target, edge_names in edges.get(current, []):
                if requested_names is None:
                    propagated_names = None if edge_names is None else frozenset(edge_names)
                elif edge_names is None:
                    propagated_names = requested_names
                else:
                    intersection = set(requested_names).intersection(edge_names)
                    if not intersection:
                        continue
                    propagated_names = frozenset(intersection)

                if propagated_names is None:
                    public_files.add(target)
                else:
                    for name in propagated_names:
                        public_symbols.add((target, name))

                next_state = (target, propagated_names)
                if next_state not in seen:
                    queue.append(next_state)

        return public_files, public_symbols

    @staticmethod
    def _is_test_support_surface(rel_path: str) -> bool:
        rel_norm = to_posix_path(rel_path).lower()
        file_name = rel_norm.rsplit("/", 1)[-1]
        heuristics = require_doctrine_mapping("dead_code_heuristics")
        test_config = heuristics.get("test_support_heuristics", {})
        path_tokens = test_config.get("path_tokens", [])
        file_suffixes = test_config.get("file_suffixes", [])
        if any(token in f"/{rel_norm}" for token in path_tokens):
            return True
        return any(file_name.endswith(s) for s in file_suffixes)

    @staticmethod
    def _is_reference_example_surface(rel_path: str) -> bool:
        rel_norm = to_posix_path(rel_path).lower()
        heuristics = require_doctrine_mapping("dead_code_heuristics")
        ref_config = heuristics.get("reference_example_heuristics", {})
        reference_tokens = ref_config.get("path_tokens", [])
        return any(token in f"/{rel_norm}" for token in reference_tokens)

    def _is_e2e_page_object_surface(self, project: str, rel_path: str, symbol: str) -> bool:
        rel_norm = to_posix_path(rel_path).lower()
        canonical = str(symbol or "").strip()
        if not canonical:
            return False

        heuristics = require_doctrine_mapping("dead_code_heuristics")
        e2e_config = heuristics.get("e2e_page_object_heuristics", {})
        project_tokens = e2e_config.get("project_tokens", [])
        path_tokens = e2e_config.get("path_tokens", [])
        symbol_regex = e2e_config.get("symbol_regex", "")

        project_root = str(self.projects.get(project) or "").lower().replace("\\", "/")
        e2e_root_hint = any(token in project_root for token in project_tokens)
        if not e2e_root_hint:
            return False

        path_hint = any(token in f"/{rel_norm}" for token in path_tokens)
        if not path_hint:
            return False

        return bool(re.fullmatch(symbol_regex, canonical))

    @staticmethod
    def _is_generated_surface(rel_path: str) -> bool:
        rel_norm = to_posix_path(rel_path).lower()
        file_name = rel_norm.rsplit("/", 1)[-1]
        heuristics = require_doctrine_mapping("dead_code_heuristics")
        gen_config = heuristics.get("generated_surfaces", {})
        file_tokens = gen_config.get("file_tokens", [])
        file_suffixes = gen_config.get("file_suffixes", [])
        
        if any(tok in file_name for tok in file_tokens):
            return True
        return any(file_name.endswith(s) for s in file_suffixes)

    def _is_deprecated_empty_export_surface(self, project: str, rel_path: str, symbol: str) -> bool:
        canonical = self._canonical_symbol_name(symbol)
        if not canonical:
            return False
        content = self._read_project_file(project, rel_path)
        if not content or "deprecated" not in content.lower():
            return False
        empty_const = re.search(
            rf"export\s+const\s+{re.escape(canonical)}\s*=\s*(?:['\"]\s*['\"]|\[\s*\]|\{{\s*\}}|null)\s*;?",
            content,
            flags=re.MULTILINE,
        )
        empty_default = re.search(
            rf"export\s+default\s+(?:['\"]\s*['\"]|\[\s*\]|\{{\s*\}}|null)\s*;?",
            content,
            flags=re.MULTILINE,
        )
        return bool(empty_const or (canonical == "default" and empty_default))

    def _is_dynamic_registry_consumed_surface(self, project: str, rel_path: str, symbol: str) -> bool:
        canonical = self._canonical_symbol_name(symbol)
        if not canonical:
            return False
        cache_key = (project, rel_path, canonical)
        if cache_key in self._dynamic_registry_usage_cache:
            return self._dynamic_registry_usage_cache[cache_key]

        content = self._read_project_file(project, rel_path)
        if not content:
            self._dynamic_registry_usage_cache[cache_key] = False
            return False

        heuristics = require_doctrine_mapping("dead_code_heuristics")
        reg_config = heuristics.get("dynamic_registry_heuristics", {})
        markers = reg_config.get("markers", [])
        if not any(marker in content for marker in markers):
            self._dynamic_registry_usage_cache[cache_key] = False
            return False

        symbol_ref = re.search(
            rf"(?:(?:['\"]{re.escape(canonical)}['\"]\s*:)|(?:\b{re.escape(canonical)}\b\s*[,}}])|(?:component\s*:\s*{re.escape(canonical)}\b)|(?:loader\s*:\s*{re.escape(canonical)}\b))",
            content,
            flags=re.MULTILINE,
        )
        result = bool(symbol_ref)
        self._dynamic_registry_usage_cache[cache_key] = result
        return result

    def _is_graphql_document_surface(self, project: str, rel_path: str) -> bool:
        rel_norm = to_posix_path(rel_path).lower()
        file_name = rel_norm.rsplit("/", 1)[-1]
        graphql_name_hints = {
            "queries.ts",
            "mutations.ts",
            "fragments.ts",
            "subscriptions.ts",
            "query.ts",
            "mutation.ts",
            "fragment.ts",
            "subscription.ts",
        }
        if "/graphql/" not in f"/{rel_norm}" and file_name not in graphql_name_hints:
            return False
        content = self._read_project_file(project, rel_path)
        if not content:
            return False
        lowered = content.lower()
        return (
            "gql`" in content
            or "graphql`" in content
            or "typeddocumentnode" in lowered
            or "documentnode" in lowered
        )

    def _match_contract_registry(self, project: str, rel_path: str, symbol: str) -> dict | None:
        if not self._contract_registry_rules:
            return None

        rel_norm = to_posix_path(rel_path)
        rel_lower = rel_norm.lower()
        file_name = rel_norm.rsplit("/", 1)[-1]
        canonical = self._canonical_symbol_name(symbol)
        if not canonical:
            return None

        for rule in self._contract_registry_rules:
            path_globs = rule.get("path_globs") or []
            if path_globs and not any(fnmatch.fnmatchcase(rel_norm, pat) for pat in path_globs):
                continue

            file_globs = rule.get("file_globs") or []
            if file_globs and not any(fnmatch.fnmatchcase(file_name, pat) for pat in file_globs):
                continue

            path_tokens = rule.get("path_tokens") or []
            if path_tokens and not any(token in f"/{rel_lower}" for token in path_tokens):
                continue

            symbol_prefixes = rule.get("symbol_prefixes") or []
            if symbol_prefixes and not any(canonical.startswith(prefix) for prefix in symbol_prefixes):
                continue

            symbol_suffixes = rule.get("symbol_suffixes") or []
            if symbol_suffixes and not any(canonical.endswith(suffix) for suffix in symbol_suffixes):
                continue

            symbol_regex = rule.get("symbol_regex") or ""
            if symbol_regex:
                try:
                    if re.search(symbol_regex, canonical) is None:
                        continue
                except re.error:
                    continue

            content_markers = rule.get("content_markers") or []
            if content_markers:
                content = self._read_project_file(project, rel_path)
                lowered = content.lower() if content else ""
                if not any(marker in lowered for marker in content_markers):
                    continue

            return {
                "rule_id": str(rule.get("id") or ""),
                "reason": str(rule.get("reason") or "contract_registry_surface"),
                "label": str(rule.get("label") or ""),
            }
        return None

    @staticmethod
    def _actionability_profile(confidence: str, reason: str) -> dict:
        reason_norm = str(reason or "")
        confidence_norm = str(confidence or "LOW").upper()
        if reason_norm == "deprecated_empty_export_surface":
            return {
                "level": "actionable",
                "score": 0.90,
                "policy": "dead_code_actionability_v1",
                "unusedness_confidence": confidence_norm.lower(),
                "remediation_confidence": "conditional",
                "intent_decision_required": True,
                "mutation_proposed": False,
                "allowed_outcomes": ["delete", "retain_contract", "unknown"],
                "why": "Deprecated empty export surface; safe removal candidate after confirming no public compatibility promise remains.",
            }
        if confidence_norm == "HIGH" and reason_norm in {"unreferenced_file_export", "constant_surface_unreferenced_file_export"}:
            return {
                "level": "manual_intent_decision",
                "score": 0.0,
                "policy": "dead_code_actionability_v1",
                "unusedness_confidence": "high",
                "remediation_confidence": "unknown",
                "intent_decision_required": True,
                "mutation_proposed": False,
                "allowed_outcomes": ["delete", "complete_integration", "retain_contract", "unknown"],
                "why": "The graph proves non-consumption, not intent. Decide whether this is deletion, incomplete integration, a retained contract, or UNKNOWN before mutation.",
            }
        if confidence_norm == "MEDIUM":
            return {
                "level": "review",
                "score": 0.60,
                "policy": "dead_code_actionability_v1",
                "unusedness_confidence": "medium",
                "remediation_confidence": "unknown",
                "intent_decision_required": True,
                "mutation_proposed": False,
                "allowed_outcomes": ["delete", "complete_integration", "retain_contract", "unknown"],
                "why": "Partially referenced file; verify contract/export intent before deletion.",
            }
        return {
            "level": "review",
            "score": 0.35,
            "policy": "dead_code_actionability_v1",
            "unusedness_confidence": confidence_norm.lower(),
            "remediation_confidence": "unknown",
            "intent_decision_required": True,
            "mutation_proposed": False,
            "allowed_outcomes": ["delete", "complete_integration", "retain_contract", "unknown"],
            "why": "Low-confidence or collision-prone signal; requires manual confirmation.",
        }

    @staticmethod
    def _is_di_container_surface(rel_path: str, symbol: str) -> bool:
        rel_norm = to_posix_path(rel_path).lower()
        canonical = str(symbol or "").strip()
        if rel_norm.endswith(".container.ts") or "/di/" in f"/{rel_norm}":
            return canonical.startswith("get") and len(canonical) > 3
        return False

    @staticmethod
    def _is_contract_payload_surface(rel_path: str, symbol: str) -> bool:
        rel_norm = to_posix_path(rel_path).lower()
        canonical = str(symbol or "").strip()
        contract_path_tokens = (
            "/dto/",
            "/dtos/",
            "/input/",
            "/inputs/",
            "/output/",
            "/outputs/",
            "/schema/",
            "/schemas/",
            "/types/",
            "/contracts/",
            "/responses/",
            "/requests/",
        )
        if not any(token in f"/{rel_norm}" for token in contract_path_tokens):
            return False
        contract_suffixes = (
            "Dto",
            "Input",
            "Output",
            "Schema",
            "Response",
            "Request",
            "Payload",
            "Params",
            "Type",
            "Types",
        )
        return any(canonical.endswith(suffix) for suffix in contract_suffixes)

    @staticmethod
    def _is_graphql_type_contract_surface(rel_path: str, symbol: str) -> bool:
        rel_norm = to_posix_path(rel_path).lower()
        canonical = str(symbol or "").strip()
        graph_type_file = (
            rel_norm.endswith("types.ts")
            or rel_norm.endswith("types.tsx")
            or rel_norm.endswith("type.ts")
            or "/graphql/" in f"/{rel_norm}"
            or "/apollo/" in f"/{rel_norm}"
        )
        if not graph_type_file:
            return False
        suffixes = (
            "Args",
            "Variables",
            "Input",
            "Payload",
            "Mutation",
            "Query",
            "Subscription",
            "Fragment",
            "Response",
        )
        return any(canonical.endswith(suffix) for suffix in suffixes)

    @staticmethod
    def _is_react_ui_surface(rel_path: str, symbol: str) -> bool:
        rel_norm = to_posix_path(rel_path).lower()
        canonical = str(symbol or "").strip()
        ui_tokens = ("/components/", "/hooks/", "/views/", "/widgets/", "/screens/")
        if not any(token in f"/{rel_norm}" for token in ui_tokens):
            return False
        if rel_norm.endswith(".tsx"):
            return True
        if canonical.startswith("use") and len(canonical) > 3 and canonical[3:4].isupper():
            return True
        if canonical[:1].isupper():
            return True
        return False

    def _is_legacy_boundary_contract(self, project: str, rel_path: str, symbol: str) -> bool:
        return False

    def _is_contract_only_surface(self, project: str, rel_path: str, symbol: str) -> bool:
        return False

    def _is_constant_surface_candidate(self, project: str, rel_path: str, symbol: str) -> bool:
        canonical = self._canonical_symbol_name(symbol)
        cache_key = (project, rel_path, canonical)
        if cache_key in self._constant_surface_cache:
            return self._constant_surface_cache[cache_key]

        rel_norm = to_posix_path(rel_path).lower()
        looks_like_constants_file = rel_norm.endswith("constants.ts") or "/constants/" in rel_norm
        looks_like_constant_symbol = bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", canonical))

        is_constant_surface = looks_like_constants_file and looks_like_constant_symbol
        self._constant_surface_cache[cache_key] = is_constant_surface
        return is_constant_surface

    def _is_type_surface_candidate(self, project: str, rel_path: str, symbol: str) -> bool:
        canonical = self._canonical_symbol_name(symbol)
        cache_key = (project, rel_path, canonical)
        if cache_key in self._type_surface_cache:
            return self._type_surface_cache[cache_key]

        rel_norm = to_posix_path(rel_path).lower()
        looks_like_types_file = (
            rel_norm.endswith("types.ts")
            or rel_norm.endswith("types.tsx")
            or "/types/" in rel_norm
        )
        looks_like_pascal = bool(re.fullmatch(r"[A-Z][A-Za-z0-9_]*", canonical))

        is_type_surface = looks_like_types_file and looks_like_pascal
        self._type_surface_cache[cache_key] = is_type_surface
        return is_type_surface

    def _type_only_export_names(self, project: str, rel_path: str, file_data: dict) -> set[str]:
        cache_key = (project, rel_path)
        cached = self._type_only_export_cache.get(cache_key)
        if cached is not None:
            return cached

        type_only = set()
        for entry in file_data.get("symbols", []) if isinstance(file_data, dict) else []:
            if not isinstance(entry, dict):
                continue
            if not entry.get("exported"):
                continue
            name = self._canonical_symbol_name(entry.get("name", ""))
            if not name:
                continue
            symbol_type = str(entry.get("type") or "").strip().lower()
            export_kind = str(entry.get("export_kind") or "").strip().lower()
            if export_kind == "type" or symbol_type in {
                "interface",
                "typealias",
                "tstypealiasdeclaration",
                "typedefinition",
                "type",
            }:
                type_only.add(name)

        self._type_only_export_cache[cache_key] = type_only
        return type_only

    def _generated_symbol_references(self, project: str, project_data: dict) -> set[str]:
        cached = self._generated_symbol_reference_cache.get(project)
        if cached is not None:
            return cached

        files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
        symbols = set()
        for rel_path in files.keys() if isinstance(files, dict) else []:
            rel_norm = to_posix_path(rel_path)
            if not self._is_generated_surface(rel_norm):
                continue
            content = self._read_project_file(project, rel_norm)
            if not content:
                continue
            for token in re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b", content):
                symbols.add(token)

        self._generated_symbol_reference_cache[project] = symbols
        return symbols

    def _generated_contract_manifest_symbols(self, project: str, project_data: dict) -> set[str]:
        """
        Build a coarse contract manifest from generated files:
        - imported symbol names used by generated code
        - exported names from generated files
        """
        cached = self._generated_contract_manifest_cache.get(project)
        if cached is not None:
            return cached

        files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
        symbols = set()
        for rel_path, file_data in files.items() if isinstance(files, dict) else []:
            rel_norm = to_posix_path(rel_path)
            if not self._is_generated_surface(rel_norm):
                continue
            if not isinstance(file_data, dict):
                continue
            for imp in file_data.get("import_records", []) or []:
                if not isinstance(imp, dict):
                    continue
                name = self._canonical_symbol_name(imp.get("name", ""))
                if name:
                    symbols.add(name)
            for exp in file_data.get("exports", []) or []:
                name = exp if isinstance(exp, str) else exp.get("name")
                canonical = self._canonical_symbol_name(name or "")
                if canonical:
                    symbols.add(canonical)

        self._generated_contract_manifest_cache[project] = symbols
        return symbols

    def _runtime_consumed_contract_symbols(self, project: str, project_data: dict) -> dict[str, set[str]]:
        """
        Runtime-consumed contracts are framework/plugin-discovered exports that may
        not appear in import graphs. Rules are doctrine-driven for universality.
        Returns {rel_path -> {symbol,...}}.
        """
        cached = self._runtime_consumed_contract_cache.get(project)
        if cached is not None:
            return cached

        rules = require_doctrine_mapping("dead_code_heuristics").get("runtime_consumed_contracts")
        files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
        result: dict[str, set[str]] = defaultdict(set)

        if not isinstance(rules, list) or not isinstance(files, dict):
            self._runtime_consumed_contract_cache[project] = result
            return result

        for rel_path, file_data in files.items():
            if not isinstance(file_data, dict):
                continue
            rel_norm = to_posix_path(rel_path)
            rel_lower = rel_norm.lower()
            file_name = rel_norm.rsplit("/", 1)[-1]
            feature_set = {str(x) for x in (file_data.get("features") or [])}
            exported_names = set()
            for exp in file_data.get("exports", []) or []:
                raw = exp if isinstance(exp, str) else exp.get("name")
                canonical = self._canonical_symbol_name(raw or "")
                if canonical:
                    exported_names.add(canonical)

            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                if rule.get("enabled", True) is False:
                    continue

                file_globs = [str(x) for x in (rule.get("file_globs") or []) if str(x).strip()]
                if file_globs and not any(fnmatch.fnmatchcase(file_name, pat) for pat in file_globs):
                    continue

                path_tokens = [to_posix_path(str(x)).lower() for x in (rule.get("path_tokens") or []) if str(x).strip()]
                if path_tokens and not any(token in f"/{rel_lower}" for token in path_tokens):
                    continue

                required_features = [str(x) for x in (rule.get("required_features") or []) if str(x).strip()]
                if required_features and not any(feat in feature_set for feat in required_features):
                    continue

                explicit_symbols = [self._canonical_symbol_name(str(x)) for x in (rule.get("symbols") or [])]
                explicit_symbols = [x for x in explicit_symbols if x]
                for sym in explicit_symbols:
                    result[rel_norm].add(sym)

                regex = str(rule.get("symbol_regex") or "").strip()
                if regex:
                    try:
                        for sym in exported_names:
                            if re.search(regex, sym):
                                result[rel_norm].add(sym)
                    except re.error:
                        continue

        self._runtime_consumed_contract_cache[project] = result
        return result

    def _ast_runtime_contract_symbols(self, project: str, project_data: dict) -> dict[str, set[str]]:
        """
        AST truth signal: symbols explicitly marked as runtime-discovered contracts
        by sequencer (Contract:RuntimeDiscovered).
        """
        cached = self._ast_runtime_contract_cache.get(project)
        if cached is not None:
            return cached

        files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
        result: dict[str, set[str]] = defaultdict(set)
        if not isinstance(files, dict):
            self._ast_runtime_contract_cache[project] = result
            return result

        for rel_path, file_data in files.items():
            if not isinstance(file_data, dict):
                continue
            rel_norm = to_posix_path(rel_path)
            for symbol in file_data.get("symbols", []) or []:
                if not isinstance(symbol, dict):
                    continue
                if not bool(symbol.get("exported")):
                    continue
                runtime_contract_flag = bool(
                    symbol.get("runtime_contract", symbol.get("runtimeContract", False))
                )
                features = {str(x) for x in (symbol.get("features") or [])}
                if (not runtime_contract_flag) and ("Contract:RuntimeDiscovered" not in features):
                    continue
                name = self._canonical_symbol_name(symbol.get("name", ""))
                if name:
                    result[rel_norm].add(name)

        self._ast_runtime_contract_cache[project] = result
        return result

    def _ast_graphql_contract_symbols(self, project: str, project_data: dict) -> dict[str, set[str]]:
        """
        AST truth signal for GraphQL document/fragment contract exports.
        """
        cached = self._ast_graphql_contract_cache.get(project)
        if cached is not None:
            return cached

        files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
        result: dict[str, set[str]] = defaultdict(set)
        if not isinstance(files, dict):
            self._ast_graphql_contract_cache[project] = result
            return result

        for rel_path, file_data in files.items():
            if not isinstance(file_data, dict):
                continue
            rel_norm = to_posix_path(rel_path)
            for symbol in file_data.get("symbols", []) or []:
                if not isinstance(symbol, dict):
                    continue
                if not bool(symbol.get("exported")):
                    continue
                features = {str(x) for x in (symbol.get("features") or [])}
                if "Contract:GraphQLDocument" not in features:
                    continue
                name = self._canonical_symbol_name(symbol.get("name", ""))
                if name:
                    result[rel_norm].add(name)

        self._ast_graphql_contract_cache[project] = result
        return result

    @staticmethod
    def _is_framework_export(rel_path: str, symbol: str) -> bool:
        normalized = to_posix_path(rel_path)
        file_name = normalized.rsplit("/", 1)[-1]
        parent_parts = normalized.split("/")
        file_lower = file_name.lower()
        normalized_lower = normalized.lower()
        symbol_norm = str(symbol or "").strip()
        symbol_lower = symbol_norm.lower()

        framework_rules = require_doctrine_mapping("dead_code_heuristics").get("framework_discovered_exports")
        for rule in framework_rules:
            # 1. Match file_name if present
            rule_file_names = rule.get("file_name")
            if rule_file_names:
                if isinstance(rule_file_names, str):
                    rule_file_names = [rule_file_names]
                if file_lower not in {name.lower() for name in rule_file_names}:
                    continue

            # 2. Match file_suffix if present
            rule_suffixes = rule.get("file_suffix")
            if rule_suffixes:
                if isinstance(rule_suffixes, str):
                    rule_suffixes = [rule_suffixes]
                if not any(file_lower.endswith(s.lower()) for s in rule_suffixes):
                    continue

            # 3. Match parent folder if present
            parent = rule.get("parent")
            if parent and parent.lower() not in {p.lower() for p in parent_parts}:
                continue

            # 4. Match path_prefix if present
            prefix = rule.get("path_prefix")
            if prefix and not normalized_lower.startswith(prefix.lower()):
                continue

            # 5. Match path_contains if present
            contains = rule.get("path_contains")
            if contains and contains.lower() not in normalized_lower:
                continue

            # 6. Match symbols
            if rule.get("all_symbols", False):
                return True
            
            rule_symbols = rule.get("symbols", [])
            if symbol_lower in {s.lower() for s in rule_symbols}:
                return True

        return False

    def _analyze_project_exports(
        self,
        *,
        project: str,
        project_data: dict,
        imported_files_for_project: set,
        namespace_imported_files_for_project: set,
        propagated_consumed_for_project: set,
        public_export_chain_files: set,
        public_export_chain_symbols: set,
        all_imported_names: set,
        symbol_usage_files_for_project: dict,
    ) -> dict:
        dead = []
        compatibility_skips = []
        legacy_contract_skips = []
        contract_surface_skips = []
        contract_registry_skips = []
        runtime_contract_skips = []
        ast_runtime_contract_skips = []
        ast_graphql_contract_skips = []
        graphql_type_contract_skips = []
        generated_manifest_skips = []
        constant_surface_skips = []
        type_surface_skips = []
        type_only_export_skips = []
        intent_allowlist_skips = []
        advisory_name_collision_skips = []
        classification_reasons = Counter()

        files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
        generated_symbol_refs = self._generated_symbol_references(project, project_data)
        generated_contract_manifest = self._generated_contract_manifest_symbols(project, project_data)
        runtime_contracts = self._runtime_consumed_contract_symbols(project, project_data)
        ast_runtime_contracts = self._ast_runtime_contract_symbols(project, project_data)
        ast_graphql_contracts = self._ast_graphql_contract_symbols(project, project_data)
        package_public_patterns = self._package_public_export_patterns(project, project_data)
        for rel_path, f_data in files.items():
            n_file = to_posix_path(rel_path)
            if not is_analysis_source_file(n_file):
                continue

            type_only_exports = self._type_only_export_names(project, rel_path, f_data)

            is_file_imported = n_file in imported_files_for_project
            if not is_file_imported:
                base_no_ext = os.path.splitext(n_file)[0]
                if base_no_ext in imported_files_for_project or base_no_ext.removesuffix("/index") in imported_files_for_project:
                    is_file_imported = True

            for sym_info in f_data.get("exports", []):
                if isinstance(sym_info, dict) and sym_info.get("type") == "ProxyExport":
                    continue

                sym = sym_info if isinstance(sym_info, str) else sym_info.get("name")
                ignored_symbols = require_doctrine_mapping("dead_code_heuristics").get("global_ignored_symbols")
                if not sym or sym in ignored_symbols:
                    continue
                canonical_sym = self._canonical_symbol_name(sym)

                if self._is_framework_export(rel_path, sym):
                    continue

                if self._is_nest_runtime_export(project, rel_path, sym):
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": self._canonical_symbol_name(sym),
                            "reason": "nestjs_runtime_export",
                        }
                    )
                    continue

                if n_file in public_export_chain_files or (n_file, canonical_sym) in public_export_chain_symbols:
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "active_public_export_chain",
                        }
                    )
                    continue

                if self._matches_package_public_export_surface(rel_path, package_public_patterns):
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": self._canonical_symbol_name(sym),
                            "reason": "package_public_export_surface",
                        }
                    )
                    continue

                if self._is_generated_surface(rel_path):
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": self._canonical_symbol_name(sym),
                            "reason": "generated_surface",
                        }
                    )
                    continue

                if self._is_test_support_surface(rel_path):
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": self._canonical_symbol_name(sym),
                            "reason": "test_support_surface",
                        }
                    )
                    continue
                if self._is_reference_example_surface(rel_path):
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": self._canonical_symbol_name(sym),
                            "reason": "reference_example_surface",
                        }
                    )
                    continue
                if self._is_e2e_page_object_surface(project, rel_path, sym):
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": self._canonical_symbol_name(sym),
                            "reason": "e2e_page_object_surface",
                        }
                    )
                    continue

                if self._is_di_container_surface(rel_path, sym):
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": self._canonical_symbol_name(sym),
                            "reason": "di_container_surface",
                        }
                    )
                    continue

                if self._is_contract_payload_surface(rel_path, sym):
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": self._canonical_symbol_name(sym),
                            "reason": "contract_payload_surface",
                        }
                    )
                    continue
                if self._is_graphql_type_contract_surface(rel_path, sym):
                    graphql_type_contract_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": self._canonical_symbol_name(sym),
                            "reason": "graphql_type_contract_surface",
                        }
                    )
                    continue

                registry_match = self._match_contract_registry(project, rel_path, sym)
                if registry_match:
                    contract_registry_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": self._canonical_symbol_name(sym),
                            "reason": registry_match.get("reason") or "contract_registry_surface",
                            "rule_id": registry_match.get("rule_id") or "",
                            "label": registry_match.get("label") or "",
                        }
                    )
                    continue

                if self._is_graphql_document_surface(project, rel_path):
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": self._canonical_symbol_name(sym),
                            "reason": "graphql_document_surface",
                        }
                    )
                    continue

                canonical_sym = self._canonical_symbol_name(sym)
                if not canonical_sym:
                    continue
                if len(canonical_sym) <= 1:
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "insignificant_symbol_surface",
                        }
                    )
                    continue
                if canonical_sym in type_only_exports:
                    type_only_export_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "type_only_export",
                        }
                    )
                    continue

                # Namespace imports (`import * as X from '...'`) intentionally
                # consume export surfaces via property access/dynamic lookup,
                # which is often not fully recoverable as named import edges.
                if n_file in namespace_imported_files_for_project:
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "namespace_import_surface",
                        }
                    )
                    continue

                if canonical_sym in (ast_runtime_contracts.get(n_file) or set()):
                    ast_runtime_contract_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "ast_runtime_contract_surface",
                        }
                    )
                    continue
                if canonical_sym in (ast_graphql_contracts.get(n_file) or set()):
                    ast_graphql_contract_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "ast_graphql_contract_surface",
                        }
                    )
                    continue

                if canonical_sym in (runtime_contracts.get(n_file) or set()):
                    runtime_contract_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "runtime_consumed_contract_surface",
                        }
                    )
                    continue

                is_sym_consumed = (n_file, canonical_sym) in propagated_consumed_for_project
                if is_sym_consumed:
                    continue

                if self._has_local_symbol_usage(project, rel_path, canonical_sym):
                    continue

                if self._is_compatibility_bridge_file(project, rel_path):
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "compatibility_bridge",
                        }
                    )
                    continue

                # Generated client layers (e.g. graphql hooks/types codegen) can consume
                # symbols without regular import edges in analyzed runtime graph.
                if canonical_sym in generated_symbol_refs:
                    compatibility_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "generated_consumer_surface",
                        }
                    )
                    continue
                if canonical_sym in generated_contract_manifest:
                    generated_manifest_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "generated_contract_manifest_surface",
                        }
                    )
                    continue

                if self._is_legacy_boundary_contract(project, rel_path, canonical_sym):
                    legacy_contract_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "legacy_boundary_contract",
                        }
                    )
                    continue

                if self._is_contract_only_surface(project, rel_path, canonical_sym):
                    contract_surface_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "contract_only_surface",
                        }
                    )
                    continue

                if self._is_dynamic_registry_consumed_surface(project, rel_path, canonical_sym):
                    runtime_contract_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "dynamic_registry_consumed_surface",
                        }
                    )
                    continue

                confidence = "HIGH"
                if is_file_imported:
                    confidence = "MEDIUM"
                if is_file_imported and canonical_sym in all_imported_names:
                    confidence = "LOW"
                if (not is_file_imported) and canonical_sym in all_imported_names:
                    confidence = "LOW"
                if confidence == "HIGH" and self._is_constant_surface_candidate(project, rel_path, canonical_sym):
                    confidence = "MEDIUM"

                external_symbol_usage = any(
                    used_file != n_file
                    for used_file in (symbol_usage_files_for_project.get(canonical_sym) or set())
                )
                if confidence == "HIGH" and external_symbol_usage:
                    confidence = "MEDIUM"
                if confidence == "HIGH" and self._is_react_ui_surface(rel_path, canonical_sym):
                    confidence = "MEDIUM"

                if confidence == "LOW" and self._is_constant_surface_candidate(project, rel_path, canonical_sym):
                    constant_surface_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "constant_surface_candidate",
                        }
                    )
                    continue

                if confidence == "LOW" and self._is_type_surface_candidate(project, rel_path, canonical_sym):
                    type_surface_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "reason": "type_surface_candidate",
                        }
                    )
                    continue

                reason = "unreferenced_file_export"
                if is_file_imported and canonical_sym in all_imported_names:
                    reason = "symbol_seen_elsewhere_name_collision"
                elif is_file_imported:
                    reason = "unconsumed_export_in_referenced_file"
                elif external_symbol_usage:
                    reason = "symbol_usage_detected_outside_import_graph"
                elif canonical_sym in all_imported_names:
                    reason = "symbol_seen_elsewhere_name_collision"
                elif confidence == "MEDIUM" and self._is_constant_surface_candidate(project, rel_path, canonical_sym):
                    reason = "constant_surface_unreferenced_file_export"
                if (
                    reason in {"unreferenced_file_export", "constant_surface_unreferenced_file_export"}
                    and self._is_deprecated_empty_export_surface(project, rel_path, canonical_sym)
                ):
                    reason = "deprecated_empty_export_surface"
                classification_reasons[reason] += 1

                # Guardrail: name-collision findings are historically the noisiest class.
                # Keep them as advisory exclusions unless additional strong proof exists.
                if reason in {"symbol_seen_elsewhere_name_collision", "symbol_usage_detected_outside_import_graph"}:
                    advisory_name_collision_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "confidence": confidence,
                            "reason": "advisory_name_collision",
                        }
                    )
                    continue

                # Guardrail: public/barrel surfaces are often intentionally broad export points.
                if self._is_barrel_public_surface(rel_path) and confidence in {"LOW", "MEDIUM"}:
                    advisory_name_collision_skips.append(
                        {
                            "project": project,
                            "file": rel_path,
                            "scoped_file": self._scoped_file(project, rel_path),
                            "symbol": canonical_sym,
                            "confidence": confidence,
                            "reason": "barrel_public_surface",
                        }
                    )
                    continue

                dead.append(
                    {
                        "project": project,
                        "file": rel_path,
                        "scoped_file": self._scoped_file(project, rel_path),
                        "symbol": canonical_sym,
                        "confidence": confidence,
                        "reason": reason,
                        "actionability": self._actionability_profile(confidence, reason),
                        "evidence": {
                            "file_imported": bool(is_file_imported),
                            "symbol_seen_globally": bool(canonical_sym in all_imported_names),
                            "local_symbol_usage": False,
                            "proxy_resolution_enabled": True,
                        },
                    }
                )
                allowlist_hit = self._allowlist_match(dead[-1])
                if allowlist_hit:
                    excluded = dict(dead[-1])
                    excluded["allowlist"] = allowlist_hit
                    intent_allowlist_skips.append(excluded)
                    dead.pop()
                    continue

        return {
            "dead": dead,
            "compatibility_exclusions": compatibility_skips,
            "legacy_boundary_exclusions": legacy_contract_skips,
            "contract_surface_exclusions": contract_surface_skips,
            "contract_registry_exclusions": contract_registry_skips,
            "runtime_contract_exclusions": runtime_contract_skips,
            "ast_runtime_contract_exclusions": ast_runtime_contract_skips,
            "ast_graphql_contract_exclusions": ast_graphql_contract_skips,
            "graphql_type_contract_exclusions": graphql_type_contract_skips,
            "generated_manifest_exclusions": generated_manifest_skips,
            "constant_surface_exclusions": constant_surface_skips,
            "type_surface_exclusions": type_surface_skips,
            "type_only_export_exclusions": type_only_export_skips,
            "intent_allowlist_exclusions": intent_allowlist_skips,
            "advisory_exclusions": advisory_name_collision_skips,
            "classification_reason_counts": dict(classification_reasons),
        }

    def run(self):
        started_at = perf_counter()
        phase_started_at = started_at
        progress = EngineProgress("dead_code_detector")
        progress.start()
        logger.info("[PHASE 3.12] Scanning for dead exports (Atlas-Powered)...")

        try:
            canonical_atlas = load_atlas_data()
        except Exception as e:
            logger.error(f"Failed to load Atlas payload: {e}")
            return
        atlas, execution_scope = project_runtime_atlas(
            canonical_atlas if isinstance(canonical_atlas, dict) else {}
        )
        if not atlas:
            logger.error("No Atlas projects matched the active runtime project scope.")
            return
        atlas_load_seconds = perf_counter() - phase_started_at
        phase_started_at = perf_counter()

        consumed_symbols = set()   # (project, normalized_path, symbol_name)
        imported_files = set()     # (project, normalized_path)
        namespace_imported_files = set()  # (project, normalized_path)
        all_imported_names = set() # cross-project for heuristics (LOW confidence)
        proxy_map = defaultdict(set)  # (project, source_path) -> {target_path, ...}
        project_symbol_usage_files = defaultdict(lambda: defaultdict(set))
        reference_total = sum(
            len((pdata or {}).get("files", {}))
            for pkey, pdata in atlas.items()
            if pkey != "symbols" and isinstance(pdata, dict)
        )
        progress.phase("reference_index", total=reference_total)
        reference_completed = 0

        for pkey, pdata in atlas.items():
            if pkey == "symbols":
                continue

            files = pdata.get("files", {})
            available_files = {to_posix_path(path) for path in files.keys()} if isinstance(files, dict) else set()
            for rel_path, f_data in files.items():
                reference_completed += 1
                progress.advance(reference_completed, current_project=pkey)
                n_curr = to_posix_path(rel_path)
                if not is_analysis_source_file(n_curr):
                    continue
                
                # 1. Capture Proxies (Star Exports / Named Re-exports)
                for sym_info in f_data.get("exports", []):
                    if isinstance(sym_info, dict) and sym_info.get("type") == "ProxyExport":
                        proxy_name = sym_info.get("name", "")
                        if proxy_name.startswith("proxy:"):
                            target = to_posix_path(proxy_name.removeprefix("proxy:"))
                            proxy_map[(pkey, n_curr)].add(target)

                # 2. Extract references (Static & Enhanced)
                for sym_info in f_data.get("symbols", []):
                    for dep in sym_info.get("dependencies", []):
                        if dep.startswith("dynamic:"):
                            # Lazy loading reference
                            target = to_posix_path(dep.removeprefix("dynamic:"))
                            imported_files.add((pkey, target))
                        elif dep.startswith("prop:") or dep.startswith("ui:"):
                            # Method or Component reference
                            clean_name = dep.split(":", 1)[1]
                            canonical_name = self._canonical_symbol_name(clean_name)
                            all_imported_names.add(canonical_name)
                            if canonical_name:
                                self._local_dependency_refs_cache[(pkey, rel_path)].add(canonical_name)
                                project_symbol_usage_files[pkey][canonical_name].add(n_curr)
                        else:
                            # Standard Identifier reference
                            canonical_name = self._canonical_symbol_name(dep)
                            all_imported_names.add(canonical_name)
                            if canonical_name:
                                self._local_dependency_refs_cache[(pkey, rel_path)].add(canonical_name)
                                project_symbol_usage_files[pkey][canonical_name].add(n_curr)

                # 3. Traditional Import Records
                for imp_info in f_data.get("import_records", []):
                    src = imp_info.get("source")
                    name = imp_info.get("name")
                    kind = str(imp_info.get("kind") or "").strip().lower()
                    if src:
                        n_src = to_posix_path(src)
                        imported_files.add((pkey, n_src))
                        if kind == "namespace":
                            namespace_imported_files.add((pkey, n_src))
                        if name:
                            canonical_name = self._canonical_symbol_name(name)
                            if kind != "namespace":
                                consumed_symbols.add((pkey, n_src, canonical_name))
                            all_imported_names.add(canonical_name)
                            if canonical_name:
                                project_symbol_usage_files[pkey][canonical_name].add(n_curr)

                # 4. Dynamic import destructuring is a real symbol consumption surface.
                # TypeScript's import graph may only preserve the dynamic target, so
                # recover named exports consumed by tests and lazy adapters here.
                content = self._read_project_file(pkey, n_curr)
                if content:
                    for src, names in self._parse_dynamic_named_imports(content):
                        n_src = self._resolve_dynamic_import_target(n_curr, src, available_files)
                        if not n_src:
                            continue
                        imported_files.add((pkey, n_src))
                        for canonical_name in names:
                            consumed_symbols.add((pkey, n_src, canonical_name))
                            all_imported_names.add(canonical_name)
                            project_symbol_usage_files[pkey][canonical_name].add(n_curr)

        # 4. Recursive Proxy Resolution (Ensure files exported via * are marked imported)
        changed = True
        while changed:
            changed = False
            new_imports = set()
            for (pkey, n_src) in imported_files:
                for target in proxy_map.get((pkey, n_src), set()):
                    if (pkey, target) not in imported_files:
                        new_imports.add((pkey, target))
                        changed = True
            imported_files.update(new_imports)

        # 5. Propagate consumed symbols over proxy/re-export chains.
        # This avoids false positives where symbols are consumed via barrel files
        # but physically defined in downstream module files.
        propagated_consumed = set(consumed_symbols)
        queue = list(consumed_symbols)
        while queue:
            pkey, n_src, canonical_name = queue.pop()
            for target in proxy_map.get((pkey, n_src), set()):
                candidate = (pkey, target, canonical_name)
                if candidate not in propagated_consumed:
                    propagated_consumed.add(candidate)
                    queue.append(candidate)

        imported_files_by_project = defaultdict(set)
        for pkey, rel in imported_files:
            imported_files_by_project[pkey].add(rel)
        namespace_imported_files_by_project = defaultdict(set)
        for pkey, rel in namespace_imported_files:
            namespace_imported_files_by_project[pkey].add(rel)

        propagated_consumed_by_project = defaultdict(set)
        for pkey, rel, symbol in propagated_consumed:
            propagated_consumed_by_project[pkey].add((rel, symbol))

        public_export_chain_files_by_project = defaultdict(set)
        public_export_chain_symbols_by_project = defaultdict(set)
        for pkey, pdata in atlas.items():
            if pkey == "symbols" or not isinstance(pdata, dict):
                continue
            files = pdata.get("files", {})
            chain_files, chain_symbols = self._public_export_chain_surfaces(
                pkey,
                files,
                pdata.get("public_contracts", {}) if isinstance(pdata.get("public_contracts"), dict) else {},
            )
            public_export_chain_files_by_project[pkey] = chain_files
            public_export_chain_symbols_by_project[pkey] = chain_symbols

        reference_index_seconds = perf_counter() - phase_started_at
        phase_started_at = perf_counter()

        doctrine_signature = self._doctrine_signature()
        allowlist_signature = self._allowlist_signature()
        imported_name_signature = self._stable_fingerprint(sorted(all_imported_names))

        dead = []
        compatibility_skips = []
        legacy_contract_skips = []
        contract_surface_skips = []
        contract_registry_skips = []
        runtime_contract_skips = []
        ast_runtime_contract_skips = []
        ast_graphql_contract_skips = []
        graphql_type_contract_skips = []
        generated_manifest_skips = []
        constant_surface_skips = []
        type_surface_skips = []
        type_only_export_skips = []
        intent_allowlist_skips = []
        advisory_skips = []
        classification_reasons = Counter()

        cache_hits = 0
        cache_misses = 0
        project_analysis_seconds = {}
        cache_projects = self._project_cache.setdefault("projects", {})
        # A scoped run must not evict caches for projects preserved in canonical Atlas.
        valid_projects = canonical_project_keys(canonical_atlas)
        stale_project_keys = [p for p in cache_projects.keys() if p not in valid_projects]
        for stale_key in stale_project_keys:
            cache_projects.pop(stale_key, None)
            self._project_cache_dirty = True

        analyzed_project_count = len(atlas)
        preserved_project_count = max(0, len(valid_projects) - analyzed_project_count)
        progress.phase(
            "project_analysis",
            total=analyzed_project_count,
            preserved_projects=preserved_project_count,
        )
        project_completed = 0

        for pkey, pdata in atlas.items():
            if pkey == "symbols":
                continue

            project_started_at = perf_counter()
            progress.checkpoint("project_start", project=pkey)
            logger.info("[PROFILE] Dead Code project analysis started: %s", pkey)

            project_signature = self._project_atlas_signature(pdata)
            project_cache_key = self._stable_fingerprint(
                {
                    "project_signature": project_signature,
                    "imported_name_signature": imported_name_signature,
                    "public_export_chain_files": sorted(public_export_chain_files_by_project.get(pkey, set())),
                    "public_export_chain_symbols": sorted(
                        f"{rel}::{symbol}"
                        for rel, symbol in public_export_chain_symbols_by_project.get(pkey, set())
                    ),
                    "allowlist_signature": allowlist_signature,
                    "doctrine_signature": doctrine_signature,
                    "engine_signature": DEAD_CODE_ENGINE_SIGNATURE,
                }
            )
            cached_entry = cache_projects.get(pkey, {})
            cached_result = cached_entry.get("result") if isinstance(cached_entry, dict) else None
            if isinstance(cached_result, dict) and str(cached_entry.get("cache_key", "")) == project_cache_key:
                cache_hits += 1
                analysis = cached_result
            else:
                cache_misses += 1
                analysis = self._analyze_project_exports(
                    project=pkey,
                    project_data=pdata,
                    imported_files_for_project=imported_files_by_project.get(pkey, set()),
                    namespace_imported_files_for_project=namespace_imported_files_by_project.get(pkey, set()),
                    propagated_consumed_for_project=propagated_consumed_by_project.get(pkey, set()),
                    public_export_chain_files=public_export_chain_files_by_project.get(pkey, set()),
                    public_export_chain_symbols=public_export_chain_symbols_by_project.get(pkey, set()),
                    all_imported_names=all_imported_names,
                    symbol_usage_files_for_project=project_symbol_usage_files.get(pkey, {}),
                )
                cache_projects[pkey] = {
                    "cache_key": project_cache_key,
                    "result": analysis,
                }
                self._project_cache_dirty = True

            dead.extend(analysis.get("dead", []))
            compatibility_skips.extend(analysis.get("compatibility_exclusions", []))
            legacy_contract_skips.extend(analysis.get("legacy_boundary_exclusions", []))
            contract_surface_skips.extend(analysis.get("contract_surface_exclusions", []))
            contract_registry_skips.extend(analysis.get("contract_registry_exclusions", []))
            runtime_contract_skips.extend(analysis.get("runtime_contract_exclusions", []))
            ast_runtime_contract_skips.extend(analysis.get("ast_runtime_contract_exclusions", []))
            ast_graphql_contract_skips.extend(analysis.get("ast_graphql_contract_exclusions", []))
            graphql_type_contract_skips.extend(analysis.get("graphql_type_contract_exclusions", []))
            generated_manifest_skips.extend(analysis.get("generated_manifest_exclusions", []))
            constant_surface_skips.extend(analysis.get("constant_surface_exclusions", []))
            type_surface_skips.extend(analysis.get("type_surface_exclusions", []))
            type_only_export_skips.extend(analysis.get("type_only_export_exclusions", []))
            intent_allowlist_skips.extend(analysis.get("intent_allowlist_exclusions", []))
            advisory_skips.extend(analysis.get("advisory_exclusions", []))
            classification_reasons.update(analysis.get("classification_reason_counts", {}))
            project_analysis_seconds[pkey] = round(perf_counter() - project_started_at, 3)
            logger.info(
                "[PROFILE] Dead Code project analysis completed: %s %.3fs",
                pkey,
                project_analysis_seconds[pkey],
            )
            project_completed += 1
            progress.advance(project_completed, current_project=pkey)

        self._persist_project_cache()
        project_analysis_total_seconds = perf_counter() - phase_started_at
        phase_started_at = perf_counter()

        dead, physical_duplicate_removed = self._deduplicate_by_physical_origin(dead)

        # Sort by confidence (HIGH first), then project, then file
        confidence_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        dead.sort(key=lambda item: (confidence_rank[item["confidence"]], item["project"], item["file"]))

        by_project_counts = Counter(item["project"] for item in dead)
        by_project_confidence = defaultdict(lambda: Counter())
        by_project_actionability = defaultdict(lambda: Counter())
        dead_by_project = defaultdict(list)
        for item in dead:
            by_project_confidence[item["project"]][item["confidence"]] += 1
            actionability_level = str((item.get("actionability") or {}).get("level") or "review")
            by_project_actionability[item["project"]][actionability_level] += 1
            dead_by_project[item["project"]].append(item)

        high_count = sum(1 for d in dead if d["confidence"] == "HIGH")
        med_count = sum(1 for d in dead if d["confidence"] == "MEDIUM")
        low_count = sum(1 for d in dead if d["confidence"] == "LOW")
        actionability_counts = Counter(str((item.get("actionability") or {}).get("level") or "review") for item in dead)

        def build_confidence(total_count: int, high: int, medium: int, low: int) -> dict:
            if total_count <= 0:
                return {
                    "score": 1.0,
                    "tier": "not_applicable",
                    "signals": {
                        "total": 0,
                        "high_ratio": 1.0,
                        "medium_ratio": 0.0,
                        "low_ratio": 0.0,
                    },
                }
            
            heuristics = require_doctrine_mapping("dead_code_heuristics")
            formula = heuristics.get("confidence_formula", {"HIGH": 1.00, "MEDIUM": 0.60, "LOW": 0.25})
            from tools.core.doctrine_contract import require_doctrine_path
            tiers = require_doctrine_path("intelligence_tuning", "confidence_tiers", expected_type=dict)

            high_ratio = high / total_count
            medium_ratio = medium / total_count
            low_ratio = low / total_count
            
            score = (formula.get("HIGH", 1.0) * high_ratio) + (formula.get("MEDIUM", 0.6) * medium_ratio) + (formula.get("LOW", 0.25) * low_ratio)
            score = max(0.0, min(1.0, round(score, 3)))
            
            if score >= tiers.get("high", 0.80):
                tier = "high"
            elif score >= tiers.get("medium", 0.60):
                tier = "medium"
            else:
                tier = "low"
            return {
                "score": score,
                "tier": tier,
                "signals": {
                    "total": total_count,
                    "high_ratio": round(high_ratio, 3),
                    "medium_ratio": round(medium_ratio, 3),
                    "low_ratio": round(low_ratio, 3),
                },
            }

        project_keys = sorted({str(key) for key in (self.projects or {}).keys()} | {str(key) for key in dead_by_project.keys()})
        by_project_payload = {}
        for project in project_keys:
            items = dead_by_project.get(project, [])
            conf = by_project_confidence[project]
            by_project_payload[project] = {
                "display_name": project_display_name(project),
                "summary": {
                    "total": len(items),
                    "high": conf.get("HIGH", 0),
                    "medium": conf.get("MEDIUM", 0),
                    "low": conf.get("LOW", 0),
                    "actionability": {
                        "actionable": by_project_actionability[project].get("actionable", 0),
                        "manual_intent_decision": by_project_actionability[project].get("manual_intent_decision", 0),
                        "review": by_project_actionability[project].get("review", 0),
                    },
                    "confidence": build_confidence(
                        len(items),
                        conf.get("HIGH", 0),
                        conf.get("MEDIUM", 0),
                        conf.get("LOW", 0),
                    ),
                },
                "items": items,
            }

        payload = {
            "meta": {
                "version": "dead_code_v2",
                "generated_by": "dead_code_detector",
                "execution_scope": execution_scope,
                "runtime_seconds": round(perf_counter() - started_at, 3),
                "imported_file_edges": len(imported_files),
                "consumed_symbol_edges": len(propagated_consumed),
                "proxy_edges": sum(len(targets) for targets in proxy_map.values()),
                "classification_reason_counts": dict(classification_reasons),
                "actionability_policy": "dead_code_actionability_v1",
                "actionability_counts": dict(actionability_counts),
                "intent_allowlist_scope": self._allowlist_scope,
                "intent_allowlist_rules_loaded": len(self._allowlist_rules),
                "intent_allowlist_exclusions": len(intent_allowlist_skips),
                "advisory_exclusions": len(advisory_skips),
                "project_cache_version": DEAD_CODE_CACHE_VERSION,
                "project_cache_hits": cache_hits,
                "project_cache_misses": cache_misses,
                "physical_origin_duplicate_removed": physical_duplicate_removed,
                "phase_seconds": {
                    "atlas_load": round(atlas_load_seconds, 3),
                    "reference_index": round(reference_index_seconds, 3),
                    "project_analysis": round(project_analysis_total_seconds, 3),
                    "payload_assembly": round(perf_counter() - phase_started_at, 3),
                },
                "project_analysis_seconds": project_analysis_seconds,
                "package_scan_stats": self._package_scan_stats,
            },
            "summary": {
                "total": len(dead),
                "high": high_count,
                "medium": med_count,
                "low": low_count,
                "actionability": {
                    "actionable": actionability_counts.get("actionable", 0),
                    "review": actionability_counts.get("review", 0),
                },
                "confidence": build_confidence(len(dead), high_count, med_count, low_count),
            },
            "items": dead,
            "by_project": by_project_payload,
            "compatibility_exclusions": compatibility_skips,
            "legacy_boundary_exclusions": legacy_contract_skips,
            "contract_surface_exclusions": contract_surface_skips,
            "contract_registry_exclusions": contract_registry_skips,
            "runtime_contract_exclusions": runtime_contract_skips,
            "ast_runtime_contract_exclusions": ast_runtime_contract_skips,
            "ast_graphql_contract_exclusions": ast_graphql_contract_skips,
            "graphql_type_contract_exclusions": graphql_type_contract_skips,
            "generated_manifest_exclusions": generated_manifest_skips,
            "constant_surface_exclusions": constant_surface_skips,
            "type_surface_exclusions": type_surface_skips,
            "type_only_export_exclusions": type_only_export_skips,
            "intent_allowlist_exclusions": intent_allowlist_skips,
            "advisory_exclusions": advisory_skips,
        }

        ensure_valid_payload("dead_code", payload)

        json_path = RAW_DIR / "dead_code.json"
        save_json_atomic(json_path, payload)
        write_current_atlas_lineage(
            artifact_id="dead_code",
            producer="tools.engines.dead_code_detector",
            artifact_payload=payload,
            atlas=canonical_atlas,
        )

        low_items = [item for item in dead if item.get("confidence") == "LOW"]
        grouped_low = defaultdict(list)
        for item in low_items:
            grouped_low[(item.get("project"), item.get("file"), item.get("reason"))].append(item)
        suggested_rules = []
        for (project, file_path, reason), items in sorted(grouped_low.items(), key=lambda kv: len(kv[1]), reverse=True):
            count = len(items)
            file_norm = to_posix_path(str(file_path or "")).lower()
            from tools.core.doctrine_contract import require_doctrine_path
            thresholds = require_doctrine_path("dead_code_heuristics", "auto_tuning_thresholds", expected_type=dict)
            if reason == "symbol_seen_elsewhere_name_collision" and count >= thresholds.get("collision_cluster", 8):
                suggested_rules.append(
                    {
                        "id": f"auto_{project.lower()}_{count}_{abs(_deterministic_hash((project, file_path, reason))) % 10000}",
                        "scope": "project",
                        "project": project,
                        "priority": 1,
                        "estimated_exclusions": count,
                        "match": {
                            "project": project,
                            "file": file_path,
                            "confidence": "LOW",
                            "reason": "symbol_seen_elsewhere_name_collision",
                        },
                        "label": "Auto-tuning: low-confidence name-collision cluster",
                        "rationale": "Same file repeatedly triggers LOW name-collision flags; likely contract/constants surface.",
                        "example_symbols": sorted({str(item.get("symbol") or "") for item in items[:6]}),
                    }
                )
                continue
            if reason == "unconsumed_export_in_referenced_file" and count >= thresholds.get("prompt_type_cluster", 10) and ("/prompts/" in file_norm or "/types/" in file_norm or file_norm.endswith("types.ts")):
                suggested_rules.append(
                    {
                        "id": f"auto_{project.lower()}_{count}_{abs(_deterministic_hash((project, file_path, reason, 'types_prompts'))) % 10000}",
                        "scope": "project",
                        "project": project,
                        "priority": 2,
                        "estimated_exclusions": count,
                        "match": {
                            "project": project,
                            "file": file_path,
                            "confidence": "LOW",
                            "reason": "unconsumed_export_in_referenced_file",
                        },
                        "label": "Auto-tuning: low-confidence prompts/types export surface",
                        "rationale": "Prompt/type surfaces are frequently imported for partial usage and can inflate LOW dead-code false positives.",
                        "example_symbols": sorted({str(item.get("symbol") or "") for item in items[:6]}),
                    }
                )

        suggested_rules = sorted(
            suggested_rules,
            key=lambda item: (-int(item.get("estimated_exclusions", 0)), int(item.get("priority", 99))),
        )[:80]
        suggested_rules_by_project = defaultdict(list)
        for rule in suggested_rules:
            suggested_rules_by_project[rule.get("project")].append(rule)
        allowlist_seed_by_project = {}
        for project, rules in sorted(suggested_rules_by_project.items()):
            allowlist_seed_by_project[project] = {
                "rules": [
                    {
                        "id": rule["id"],
                        "enabled": False,
                        "label": rule["label"],
                        "match": rule["match"],
                    }
                    for rule in rules
                ]
            }

        tuning_payload = {
            "meta": {"kind": "dead_code_tuning", "version": "v1"},
            "summary": {
                "low_total": len(low_items),
                "group_count": len(grouped_low),
                "suggested_rule_count": len(suggested_rules),
                "estimated_exclusions_total": sum(int(item.get("estimated_exclusions", 0) or 0) for item in suggested_rules),
            },
            "suggested_rules": suggested_rules,
            "allowlist_seed_by_project": allowlist_seed_by_project,
        }
        save_json_atomic(RAW_DIR / "dead_code_tuning.json", tuning_payload)

        tuning_md_lines = [
            "# Dead Code Tuning Suggestions",
            "",
            f"- LOW-confidence dead-code items: `{tuning_payload['summary']['low_total']}`",
            f"- Suggested rules: `{tuning_payload['summary']['suggested_rule_count']}`",
            f"- Estimated exclusions if accepted: `{tuning_payload['summary']['estimated_exclusions_total']}`",
            "",
            "## Suggested Rules",
            "",
            "| Priority | Project | Est. Exclusions | Match | Label |",
            "|---:|---|---:|---|---|",
        ]
        for rule in suggested_rules[:120]:
            match = rule.get("match", {})
            match_text = ", ".join(f"{k}={v}" for k, v in match.items())
            tuning_md_lines.append(
                f"| {rule.get('priority', 99)} | `{rule.get('project', 'UNKNOWN')}` | {int(rule.get('estimated_exclusions', 0) or 0)} | `{match_text}` | {rule.get('label', '')} |"
            )
        tuning_md_lines.extend([
            "",
            "## Usage",
            "",
            "- Copy `allowlist_seed_by_project.<PROJECT>.rules` into `config/golden/projects/<PROJECT>/dead_code_intent_allowlist.json`.",
            "- Keep rules disabled initially, run pipeline, then enable only verified true false-positive clusters.",
        ])
        save_text_atomic(REPORTS_DIR / "dead_code_tuning.md", "\n".join(tuning_md_lines))

        md_lines = [
            "# Dead Code Report",
            "",
            f"**{len(dead)}** suspended exports found.",
            f"- **{high_count}** High Confidence (File completely unreferenced)",
            f"- **{med_count}** Medium Confidence (File imported, but symbol not explicitly consumed)",
            f"- **{low_count}** Low Confidence (Symbol used globally, possible synthetic/star export)",
            f"- **Actionable Candidates:** `{actionability_counts.get('actionable', 0)}`",
            f"- **Manual Intent Decisions:** `{actionability_counts.get('manual_intent_decision', 0)}`",
            f"- **Review Candidates:** `{actionability_counts.get('review', 0)}`",
            f"- **Confidence Score:** `{payload['summary']['confidence']['score']}` (`{payload['summary']['confidence']['tier']}`)",
        ]

        if compatibility_skips:
            md_lines.append(
                f"- **{len(compatibility_skips)}** Compatibility bridge exports excluded from dead-code classification"
            )
        if legacy_contract_skips:
            md_lines.append(
                f"- **{len(legacy_contract_skips)}** Legacy boundary contracts excluded from dead-code classification"
            )
        if contract_surface_skips:
            md_lines.append(
                f"- **{len(contract_surface_skips)}** Contract-only surfaces excluded from dead-code classification"
            )
        if contract_registry_skips:
            md_lines.append(
                f"- **{len(contract_registry_skips)}** Registry-matched contract surfaces excluded from dead-code classification"
            )
        if runtime_contract_skips:
            md_lines.append(
                f"- **{len(runtime_contract_skips)}** Runtime-consumed contract exports excluded from dead-code classification"
            )
        if ast_runtime_contract_skips:
            md_lines.append(
                f"- **{len(ast_runtime_contract_skips)}** AST runtime-contract exports excluded from dead-code classification"
            )
        if ast_graphql_contract_skips:
            md_lines.append(
                f"- **{len(ast_graphql_contract_skips)}** AST GraphQL contract exports excluded from dead-code classification"
            )
        if graphql_type_contract_skips:
            md_lines.append(
                f"- **{len(graphql_type_contract_skips)}** GraphQL type-contract exports excluded from dead-code classification"
            )
        if generated_manifest_skips:
            md_lines.append(
                f"- **{len(generated_manifest_skips)}** Generated-contract-manifest exports excluded from dead-code classification"
            )
        if constant_surface_skips:
            md_lines.append(
                f"- **{len(constant_surface_skips)}** Constant surfaces excluded from low-confidence dead-code classification"
            )
        if type_surface_skips:
            md_lines.append(
                f"- **{len(type_surface_skips)}** Type surfaces excluded from low-confidence dead-code classification"
            )
        if type_only_export_skips:
            md_lines.append(
                f"- **{len(type_only_export_skips)}** Type-only exports excluded from dead-code classification"
            )
        if intent_allowlist_skips:
            md_lines.append(
                f"- **{len(intent_allowlist_skips)}** Intent-allowlisted exports excluded from dead-code output"
            )
        if advisory_skips:
            md_lines.append(
                f"- **{len(advisory_skips)}** Advisory-only exports excluded from dead-code output"
            )

        md_lines.extend([
            "",
            "## By Project",
            "",
            "| Project | Total | High | Medium | Low |",
            "|---|---:|---:|---:|---:|",
        ])

        for project, total in sorted(by_project_counts.items(), key=lambda pair: pair[1], reverse=True):
            conf = by_project_confidence[project]
            md_lines.append(
                f"| {project_display_name(project)} [{project}] | {total} | {conf.get('HIGH', 0)} | {conf.get('MEDIUM', 0)} | {conf.get('LOW', 0)} |"
            )

        md_lines.extend([
            "",
            "## Actionability",
            "",
            "| Project | Actionable | Manual Intent Decision | Review |",
            "|---|---:|---:|---:|",
        ])
        for project in sorted(dead_by_project.keys()):
            ac = by_project_actionability[project]
            md_lines.append(
                f"| {project_display_name(project)} [{project}] | {ac.get('actionable', 0)} | {ac.get('manual_intent_decision', 0)} | {ac.get('review', 0)} |"
            )

        md_lines.extend([
            "",
            "## Top Entries (By Project)",
            ""
        ])

        for project in sorted(dead_by_project.keys()):
            p_items = dead_by_project[project]
            md_lines.append(f"### {project_display_name(project)} ({len(p_items)})")
            md_lines.append("")
            md_lines.append("| Confidence | File | Symbol |")
            md_lines.append("|---|---|---|")
            for item in p_items[:100]:
                namespaced_file = f"{item['project']}::{item['file']}"
                actionability = (item.get("actionability") or {}).get("level", "review")
                md_lines.append(f"| {item['confidence']} ({actionability}) | `{namespaced_file}` | `{item['symbol']}` |")
            if len(p_items) > 100:
                md_lines.append(f"| ... | *and {len(p_items) - 100} more* | |")
            md_lines.append("")

        if compatibility_skips:
            md_lines.extend([
                "",
                "## Compatibility Bridge Exclusions",
                "",
                "| Project | File | Symbol | Reason |",
                "|---|---|---|---|",
            ])
            for item in compatibility_skips[:100]:
                namespaced_file = f"{item['project']}::{item['file']}"
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{item['reason']}` |"
                )
            if len(compatibility_skips) > 100:
                md_lines.append(
                    f"\n*... and {len(compatibility_skips) - 100} more compatibility exclusions omitted for brevity.*"
                )

        if legacy_contract_skips:
            md_lines.extend([
                "",
                "## Legacy Boundary Contract Exclusions",
                "",
                "| Project | File | Symbol | Reason |",
                "|---|---|---|---|",
            ])
            for item in legacy_contract_skips[:100]:
                namespaced_file = f"{item['project']}::{item['file']}"
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{item['reason']}` |"
                )
            if len(legacy_contract_skips) > 100:
                md_lines.append(
                    f"\n*... and {len(legacy_contract_skips) - 100} more legacy-boundary exclusions omitted for brevity.*"
                )

        if contract_surface_skips:
            md_lines.extend([
                "",
                "## Contract-Only Surface Exclusions",
                "",
                "| Project | File | Symbol | Reason |",
                "|---|---|---|---|",
            ])
            for item in contract_surface_skips[:100]:
                namespaced_file = f"{item['project']}::{item['file']}"
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{item['reason']}` |"
                )
            if len(contract_surface_skips) > 100:
                md_lines.append(
                    f"\n*... and {len(contract_surface_skips) - 100} more contract-only exclusions omitted for brevity.*"
                )

        if contract_registry_skips:
            md_lines.extend([
                "",
                "## Contract Registry Exclusions",
                "",
                "| Project | File | Symbol | Rule | Reason |",
                "|---|---|---|---|---|",
            ])
            for item in contract_registry_skips[:150]:
                namespaced_file = f"{item['project']}::{item['file']}"
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{item.get('rule_id', '')}` | `{item['reason']}` |"
                )
            if len(contract_registry_skips) > 150:
                md_lines.append(
                    f"\n*... and {len(contract_registry_skips) - 150} more contract-registry exclusions omitted for brevity.*"
                )

        if runtime_contract_skips:
            md_lines.extend([
                "",
                "## Runtime Contract Exclusions",
                "",
                "| Project | File | Symbol | Reason |",
                "|---|---|---|---|",
            ])
            for item in runtime_contract_skips[:120]:
                namespaced_file = f"{item['project']}::{item['file']}"
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{item['reason']}` |"
                )
            if len(runtime_contract_skips) > 120:
                md_lines.append(
                    f"\n*... and {len(runtime_contract_skips) - 120} more runtime-contract exclusions omitted for brevity.*"
                )

        if ast_runtime_contract_skips:
            md_lines.extend([
                "",
                "## AST Runtime Contract Exclusions",
                "",
                "| Project | File | Symbol | Reason |",
                "|---|---|---|---|",
            ])
            for item in ast_runtime_contract_skips[:120]:
                namespaced_file = f"{item['project']}::{item['file']}"
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{item['reason']}` |"
                )
            if len(ast_runtime_contract_skips) > 120:
                md_lines.append(
                    f"\n*... and {len(ast_runtime_contract_skips) - 120} more ast-runtime exclusions omitted for brevity.*"
                )

        if ast_graphql_contract_skips:
            md_lines.extend([
                "",
                "## AST GraphQL Contract Exclusions",
                "",
                "| Project | File | Symbol | Reason |",
                "|---|---|---|---|",
            ])
            for item in ast_graphql_contract_skips[:120]:
                namespaced_file = f"{item['project']}::{item['file']}"
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{item['reason']}` |"
                )
            if len(ast_graphql_contract_skips) > 120:
                md_lines.append(
                    f"\n*... and {len(ast_graphql_contract_skips) - 120} more ast-graphql exclusions omitted for brevity.*"
                )

        if graphql_type_contract_skips:
            md_lines.extend([
                "",
                "## GraphQL Type Contract Exclusions",
                "",
                "| Project | File | Symbol | Reason |",
                "|---|---|---|---|",
            ])
            for item in graphql_type_contract_skips[:120]:
                namespaced_file = f"{item['project']}::{item['file']}"
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{item['reason']}` |"
                )
            if len(graphql_type_contract_skips) > 120:
                md_lines.append(
                    f"\n*... and {len(graphql_type_contract_skips) - 120} more graphql-type exclusions omitted for brevity.*"
                )

        if generated_manifest_skips:
            md_lines.extend([
                "",
                "## Generated Contract Manifest Exclusions",
                "",
                "| Project | File | Symbol | Reason |",
                "|---|---|---|---|",
            ])
            for item in generated_manifest_skips[:120]:
                namespaced_file = f"{item['project']}::{item['file']}"
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{item['reason']}` |"
                )
            if len(generated_manifest_skips) > 120:
                md_lines.append(
                    f"\n*... and {len(generated_manifest_skips) - 120} more generated-manifest exclusions omitted for brevity.*"
                )

        if constant_surface_skips:
            md_lines.extend([
                "",
                "## Constant Surface Exclusions",
                "",
                "| Project | File | Symbol | Reason |",
                "|---|---|---|---|",
            ])
            for item in constant_surface_skips[:100]:
                namespaced_file = f"{item['project']}::{item['file']}"
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{item['reason']}` |"
                )
            if len(constant_surface_skips) > 100:
                md_lines.append(
                    f"\n*... and {len(constant_surface_skips) - 100} more constant-surface exclusions omitted for brevity.*"
                )

        if type_surface_skips:
            md_lines.extend([
                "",
                "## Type Surface Exclusions",
                "",
                "| Project | File | Symbol | Reason |",
                "|---|---|---|---|",
            ])
            for item in type_surface_skips[:100]:
                namespaced_file = f"{item['project']}::{item['file']}"
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{item['reason']}` |"
                )
            if len(type_surface_skips) > 100:
                md_lines.append(
                    f"\n*... and {len(type_surface_skips) - 100} more type-surface exclusions omitted for brevity.*"
                )

        if type_only_export_skips:
            md_lines.extend([
                "",
                "## Type-Only Export Exclusions",
                "",
                "| Project | File | Symbol | Reason |",
                "|---|---|---|---|",
            ])
            for item in type_only_export_skips[:100]:
                namespaced_file = f"{item['project']}::{item['file']}"
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{item['reason']}` |"
                )
            if len(type_only_export_skips) > 100:
                md_lines.append(
                    f"\n*... and {len(type_only_export_skips) - 100} more type-only exclusions omitted for brevity.*"
                )

        if intent_allowlist_skips:
            md_lines.extend([
                "",
                "## Intent Allowlist Exclusions",
                "",
                "| Project | File | Symbol | Rule | Scope |",
                "|---|---|---|---|---|",
            ])
            for item in intent_allowlist_skips[:150]:
                namespaced_file = f"{item['project']}::{item['file']}"
                allow = item.get("allowlist", {}) if isinstance(item.get("allowlist"), dict) else {}
                md_lines.append(
                    f"| {item['project']} | `{namespaced_file}` | `{item['symbol']}` | `{allow.get('rule_id', '')}` | `{allow.get('scope', '')}` |"
                )
            if len(intent_allowlist_skips) > 150:
                md_lines.append(
                    f"\n*... and {len(intent_allowlist_skips) - 150} more intent-allowlist exclusions omitted for brevity.*"
                )

        md_path = REPORTS_DIR / "dead_code_report.md"
        save_text_atomic(md_path, "\n".join(md_lines))

        logger.info(f"Dead code analysis complete: {len(dead)} unused exports found")
        logger.info(f"Report saved to reports/{md_path.name}")
        progress.complete(
            "PASS",
            reference_files=reference_completed,
            projects=project_completed,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            findings=len(dead),
        )


if __name__ == "__main__":
    DeadCodeDetector().run()
