import os
import json
import re
import hashlib
import copy
import concurrent.futures
import queue
import sys
from bisect import bisect_left
from pathlib import Path
from time import perf_counter
from typing import Dict, List

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CONFIG_FILE, ROOT, RAW_DIR, SOURCE_EXTENSIONS, SKIP_DIRS, normalize_path, ensure_output_dir, save_json_atomic, DYNAMIC_CONFIG
from tools.core.artifact_validator import ensure_valid_payload
from tools.core.json_io import load_json_file, raw_artifact_content_fingerprint
from tools.core.path_engine import get_alias_map, reset_path_resolution_caches, resolve_project_import, to_posix_path
from tools.core.polyglot_imports import (
    extract_go_qualified_imports,
    extract_imports as extract_polyglot_imports,
    extract_typescript_import_evidence,
)
from tools.core.source_files import count_source_lines, is_analysis_source_file
from tools.core.language_registry import extension_language_map, extensions_for_language, language_for_extension, structure_extensions
from tools.core.language_agnostic_symbols import canonical_symbol_type, normalization_profile_for_language
from tools.core.projects_registry import resolve_runtime_projects
from tools.core.repository_topology import (
    is_project_owned_path,
    project_ownership_exclusions,
    prune_owned_walk_dirs,
)
from tools.core.logger import logger
from tools.core.state_flow import summarize_state_flow_features
from tools.core.atlas_integrity import build_atlas_commit, validate_atlas_commit
from tools.core.analysis_snapshot_lineage import load_atlas_commit
from tools.core.workload_profile import (
    ast_batch_strategy,
    atlas_project_worker_count as resolve_atlas_project_worker_count,
    build_workload_profile,
)
from tools.core.package_contracts import build_package_public_contracts
from tools.core.honesty_telemetry import record_honesty_event
from tools.core.operational_limits import atlas_batch_sequencer_timeout_seconds
from tools.core.subprocess_telemetry import run_observed_subprocess
from tools.core.node_ast_worker import NodeAstWorkerError, NodeAstWorkerSession

# Paths
CODE_MAPS_DIR = Path(__file__).resolve().parent.parent.parent


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


GLOBAL_ATLAS_CACHE = SizeBoundedDict(max_size=10)
AST_CONTRACT_VERSION = "v18.6-syntax-import-evidence"
PYTHON_SEQUENCER = Path(__file__).resolve().parent / "ast_sequencer_python.py"
JAVA_SEQUENCER = Path(__file__).resolve().parent / "ast_sequencer_java.py"
CS_SEQUENCER = Path(__file__).resolve().parent / "ast_sequencer_cs.py"
GO_SEQUENCER = Path(__file__).resolve().parent / "ast_sequencer_go.py"
STD_EXPORT_RE = re.compile(r"export\s+(?:type\s+)?(?:function|class|const|let|var|interface|type)\s+([a-zA-Z0-9_]+)")
NAMED_EXPORT_RE = re.compile(r"export\s+(?:type\s+)?\{(.*?)\}", re.DOTALL)
EXPORT_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
STRUCTURE_FILE_EXTENSIONS = tuple(sorted(structure_extensions()))
AST_FILE_EXTENSIONS = tuple(sorted(SOURCE_EXTENSIONS))
STATIC_NAMED_IMPORT_RE = re.compile(
    r"import\s+(?:type\s+)?(?:[A-Za-z_$][A-Za-z0-9_$]*\s*,\s*)?\{(.*?)\}\s+from\s+['\"](.+?)['\"]",
    re.DOTALL,
)
STATIC_NAMESPACE_IMPORT_RE = re.compile(
    r"import\s+(?:type\s+)?(?:[A-Za-z_$][A-Za-z0-9_$]*\s*,\s*)?\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)\s+from\s+['\"](.+?)['\"]"
)
REEXPORT_NAMED_RE = re.compile(r"export\s+(?:type\s+)?\{(.*?)\}\s+from\s+['\"](.+?)['\"]", re.DOTALL)
DYNAMIC_MEMBER_RE = re.compile(
    r"import\(\s*['\"](.+?)['\"]\s*\)\s*\.then\(\s*\(?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)?\s*=>[\s\S]*?\b\2\.([A-Za-z_][A-Za-z0-9_]*)",
    re.DOTALL
)


def _decode_node_batch_response(
    stdout: str,
    *,
    request_id: str,
    expected_paths: set[str],
) -> tuple[dict, dict, str | None]:
    try:
        decoded = json.loads(stdout.strip() or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}, {}, "malformed_json"
    if not isinstance(decoded, dict):
        return {}, {}, "response_not_object"
    batch_meta = decoded.get("batchMeta", {})
    response_results = decoded.get("results", {})
    if not isinstance(batch_meta, dict) or not isinstance(response_results, dict):
        return {}, {}, "response_envelope_invalid"
    if str(batch_meta.get("requestId") or "") != request_id:
        return {}, batch_meta, "request_identity_mismatch"
    expected_count = len(expected_paths)
    if (
        int(batch_meta.get("filesRequested", -1) or -1) != expected_count
        or int(batch_meta.get("filesReported", -1) or -1) != expected_count
    ):
        return {}, batch_meta, "response_count_mismatch"
    if set(response_results) != expected_paths:
        return {}, batch_meta, "response_file_set_mismatch"
    return response_results, batch_meta, None


FINGERPRINT_CHUNK_SIZE = 4096


class AtlasStagingWriteError(RuntimeError):
    """Raised when resumable Atlas checkpoints cannot be persisted safely."""


def atlas_staging_producer_contract() -> str:
    """Fingerprint every producer/config surface that can change staged AST semantics."""
    digest = hashlib.sha256()
    digest.update(AST_CONTRACT_VERSION.encode("utf-8"))
    producer_paths = [
        Path(__file__),
        CODE_MAPS_DIR / "tools" / "engines" / "ast_sequencer.cjs",
        PYTHON_SEQUENCER,
        JAVA_SEQUENCER,
        CS_SEQUENCER,
        GO_SEQUENCER,
        CODE_MAPS_DIR / "tools" / "core" / "config.py",
        CODE_MAPS_DIR / "tools" / "core" / "source_files.py",
        CODE_MAPS_DIR / "tools" / "core" / "language_registry.py",
        CODE_MAPS_DIR / "tools" / "core" / "language_agnostic_symbols.py",
        CODE_MAPS_DIR / "tools" / "core" / "polyglot_imports.py",
        CODE_MAPS_DIR / "tools" / "core" / "path_engine.py",
        CODE_MAPS_DIR / "tools" / "core" / "state_flow.py",
        CODE_MAPS_DIR / "tools" / "core" / "repository_topology.py",
        CODE_MAPS_DIR / "tools" / "core" / "projects_registry.py",
        Path(CONFIG_FILE),
        Path(CONFIG_FILE).parent / "architecture_doctrine.json",
        Path(CONFIG_FILE).parent / "language_registry.json",
        Path(CONFIG_FILE).parent / "language_agnostic_symbols.json",
    ]
    for producer_path in producer_paths:
        resolved = producer_path.resolve()
        digest.update(resolved.as_posix().encode("utf-8", errors="replace"))
        if resolved.exists():
            digest.update(resolved.read_bytes())
        else:
            digest.update(b"<missing>")
    return f"atlas-staging-v1:{digest.hexdigest()}"


def scoped_change_ref(project_key: str, rel_path: str) -> str:
    """Return the canonical cross-project change node consumed downstream."""
    normalized = str(rel_path or "").replace("\\", "/").strip("/")
    return f"{str(project_key or '').strip()}::{normalized}"


def _raw_import_sources_not_in_records(raw_imports, records):
    """Return raw import specifiers that have no rich import record yet."""
    seen_raw_sources = {
        str(record.get("raw_source") or "")
        for record in records or []
        if isinstance(record, dict)
    }
    return [
        str(source)
        for source in raw_imports or []
        if isinstance(source, str) and source and source not in seen_raw_sources
    ]


def _strip_comments_for_scan(content: str) -> str:
    """
    Remove line/block comments before regex-based import/export scanning.
    Prevents commented legacy code from being interpreted as live exports.
    """
    without_blocks = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    without_lines = re.sub(r"//.*?$", "", without_blocks, flags=re.MULTILINE)
    return without_lines

def _mtime_close(prev_value, current_value, epsilon: float = 0.01) -> bool:
    try:
        return abs(float(prev_value) - float(current_value)) <= epsilon
    except (TypeError, ValueError):
        return False


def _sanitize_named_export_token(raw_token: str) -> str:
    token = str(raw_token or "").strip().rstrip(";")
    if not token:
        return ""
    if token.startswith("type "):
        token = token[5:].strip()
    token = token.split(" as ")[-1].strip().rstrip(";")
    if not token:
        return ""
    if not EXPORT_IDENTIFIER_RE.fullmatch(token):
        return ""
    return token


def _extract_named_exports(content: str) -> list[str]:
    named_matches = NAMED_EXPORT_RE.findall(content)
    exports: list[str] = []
    for nm in named_matches:
        no_block_comments = re.sub(r"/\*.*?\*/", "", nm, flags=re.DOTALL)
        no_comments = re.sub(r"//.*", "", no_block_comments)
        for token in no_comments.split(","):
            cleaned = _sanitize_named_export_token(token)
            if cleaned:
                exports.append(cleaned)
    return exports


def _extract_exports_for_language(scan_content: str, language: str, ast_symbols: list[dict]) -> list[str]:
    language_norm = str(language or "").lower()
    if language_norm in {"typescript", "javascript", "vue"}:
        std_exports = STD_EXPORT_RE.findall(scan_content)
        named_exports = _extract_named_exports(scan_content)
        return sorted(list(set(std_exports + named_exports)))

    exportable_types = {"function", "class", "variable", "constant"}
    exports = []
    for symbol in ast_symbols:
        if not isinstance(symbol, dict):
            continue
        if not bool(symbol.get("exported")):
            continue
        symbol_type = str(symbol.get("type") or "").lower()
        if symbol_type not in exportable_types:
            continue
        name = str(symbol.get("name") or "").strip()
        if name and EXPORT_IDENTIFIER_RE.fullmatch(name):
            exports.append(name)
    return sorted(set(exports))

def cached_atlas_covers_projects(atlas_data: Dict[str, Dict], expected_projects) -> bool:
    if not atlas_data:
        return False
    expected = {str(project) for project in expected_projects}
    cached = set(atlas_data.keys())
    return expected.issubset(cached)


def previous_atlas_required_for_generation(
    stale_projects,
    expected_projects,
    *,
    bounded_projection: bool,
) -> bool:
    """Keep prior document truth only when this run cannot replace its full scope."""

    if bounded_projection or stale_projects is None:
        return True
    stale = {str(project) for project in stale_projects}
    expected = {str(project) for project in expected_projects}
    return not expected.issubset(stale)


def atlas_state_payload_reuse_hint(
    previous_atlas: object,
    current_atlas: object,
    canonical_fingerprint: str,
) -> dict[str, str]:
    """Authorize state reuse only for exact prior/current object equality."""
    if not isinstance(previous_atlas, dict) or not previous_atlas:
        return {}
    if not isinstance(current_atlas, dict) or previous_atlas != current_atlas:
        return {}
    fingerprint = str(canonical_fingerprint or "")
    if not fingerprint.startswith("sqlite:"):
        return {}
    payload_sha = fingerprint.split(":", 1)[1].strip()
    if not payload_sha:
        return {}
    return {
        "expected_payload_sha": payload_sha,
        "equality_contract": "exact_previous_atlas_object_equality_v1",
    }


def preserve_unselected_bounded_projects(
    atlas: Dict[str, Dict],
    previous_atlas: Dict[str, Dict],
    *,
    bounded_projection: bool,
) -> list[str]:
    """Carry excluded project truth through any bounded canonical generation."""
    if not bounded_projection or not isinstance(previous_atlas, dict):
        return []
    preserved: list[str] = []
    for project_key, project_data in previous_atlas.items():
        if project_key == "symbols" or project_key in atlas or not isinstance(project_data, dict):
            continue
        atlas[project_key] = copy.deepcopy(project_data)
        preserved.append(str(project_key))
    return sorted(preserved)


def bounded_atlas_snapshot_scope(
    atlas: Dict[str, Dict],
    selected_projects,
    surgical_files_by_project,
    *,
    project_filter_active: bool,
) -> dict[str, list[str]] | None:
    if surgical_files_by_project:
        return {
            str(project_key): sorted(
                str(path).replace("\\", "/").strip("/") for path in paths
            )
            for project_key, paths in surgical_files_by_project.items()
        }
    if not project_filter_active:
        return None
    return {
        str(project_key): sorted(
            str(path).replace("\\", "/").strip("/")
            for path in ((atlas.get(project_key) or {}).get("files") or {})
        )
        for project_key in selected_projects
    }


def apply_global_semantic_bridge(
    atlas_by_project: Dict[str, Dict],
    *,
    mutable_project_keys: set[str] | None = None,
) -> int:
    global_api_registry: dict[str, str] = {}
    for project_key, atlas in atlas_by_project.items():
        for rel_path, file_data in (atlas.get("files") or {}).items():
            for feature in file_data.get("features", []):
                if feature.startswith("route_path:"):
                    global_api_registry[feature.split(":", 1)[1]] = (
                        f"{project_key}::{rel_path}"
                    )

    linked = 0
    for project_key, atlas in atlas_by_project.items():
        if mutable_project_keys is not None and project_key not in mutable_project_keys:
            continue
        for rel_path, file_data in (atlas.get("files") or {}).items():
            for candidate in file_data.get("api_candidates", []):
                target_id = global_api_registry.get(candidate)
                if not target_id or target_id == f"{project_key}::{rel_path}":
                    continue
                dependencies = atlas.setdefault("dependencies", {}).setdefault(rel_path, [])
                if target_id not in dependencies:
                    dependencies.append(target_id)
                    linked += 1
                    logger.info(
                        "[GLOBAL BRIDGE] Linked %s::%s -> %s via route %s",
                        project_key,
                        rel_path,
                        target_id,
                        candidate,
                    )
    return linked


def atlas_generation_mode(effective_stale_projects, project_keys, surgical_files) -> str:
    if surgical_files:
        return "surgical"
    if effective_stale_projects is None:
        return "full"
    if set(effective_stale_projects) >= set(project_keys):
        return "full"
    return "incremental"


def _parser_evidence_is_reusable(evidence) -> bool:
    """Reuse real parser observations, including empty files and syntax diagnostics."""
    return (
        isinstance(evidence, dict)
        and evidence.get("reported_by_adapter") is True
        and evidence.get("status") in {"observed", "degraded"}
        and bool(evidence.get("parser_kind"))
        and evidence.get("parser_kind") != "unavailable"
    )


def file_contract_is_current(file_data: Dict) -> bool:
    if not isinstance(file_data, dict):
        return False
    if file_data.get("ast_contract_version") != AST_CONTRACT_VERSION:
        return False
    if not _parser_evidence_is_reusable(file_data.get("parser_evidence")):
        return False
    required_file_keys = {
        "project_key",
        "atlas_rel_path",
        "workspace_rel",
        "repo_relative_path",
        "target_ref",
    }
    if not required_file_keys.issubset(file_data.keys()):
        return False

    symbols = file_data.get("symbols", [])
    if not isinstance(symbols, list):
        return False

    for symbol in symbols:
        if not isinstance(symbol, dict):
            return False
        required_keys = {
            "exported",
            "export_kind",
            "modifiers",
            "extends",
            "implements",
            "members",
            "member_details",
            "module_specifier",
            "exported_names",
            "dependency_imports",
            "dynamic_imports",
            "ui_dependencies",
            "architectural_markers",
            "runtime_contract",
            "runtime_contract_kind",
            "state_flow",
            "logic_dna",
            "semantic_signature",
            "canonical_symbol_type",
            "framework_tags",
            "normalization_profile",
            "semantic_depth",
            "logic_dna_kind",
            "normalization_confidence",
            "parser_kind",
            "parser_version",
        }
        if not required_keys.issubset(symbol.keys()):
            return False
    return True


def _utf16_offset_to_utf8_byte_offset(content: str, offset: int) -> int:
    """Translate a JavaScript/TypeScript parser offset into the byte contract."""

    if offset < 0:
        raise ValueError(f"negative UTF-16 offset: {offset}")
    utf16_units = 0
    utf8_bytes = 0
    for char in content:
        if utf16_units == offset:
            return utf8_bytes
        char_units = 2 if ord(char) > 0xFFFF else 1
        if utf16_units + char_units > offset:
            raise ValueError(f"UTF-16 offset splits a surrogate pair: {offset}")
        utf16_units += char_units
        utf8_bytes += len(char.encode("utf-8"))
    if utf16_units == offset:
        return utf8_bytes
    raise ValueError(
        f"UTF-16 offset exceeds source length: offset={offset}, units={utf16_units}"
    )


def _utf16_offsets_to_utf8_byte_offsets(content: str, offsets) -> Dict[int, int]:
    """Resolve many compiler offsets with one bounded pass over source content."""

    requested_set = set()
    for offset in offsets:
        try:
            normalized_offset = int(offset)
        except (TypeError, ValueError):
            continue
        if normalized_offset >= 0:
            requested_set.add(normalized_offset)
    requested = sorted(requested_set)
    if not requested:
        return {}
    if content.isascii():
        return {offset: offset for offset in requested if offset <= len(content)}

    resolved: Dict[int, int] = {}
    requested_index = 0
    utf16_units = 0
    utf8_bytes = 0
    for char in content:
        while requested_index < len(requested) and requested[requested_index] == utf16_units:
            resolved[requested[requested_index]] = utf8_bytes
            requested_index += 1

        char_units = 2 if ord(char) > 0xFFFF else 1
        next_utf16_units = utf16_units + char_units
        while (
            requested_index < len(requested)
            and utf16_units < requested[requested_index] < next_utf16_units
        ):
            # Keep invalid surrogate-splitting offsets unresolved so the canonical
            # single-offset validator raises its existing precise error on use.
            requested_index += 1
        utf16_units = next_utf16_units
        utf8_bytes += len(char.encode("utf-8"))

    while requested_index < len(requested) and requested[requested_index] == utf16_units:
        resolved[requested[requested_index]] = utf8_bytes
        requested_index += 1
    return resolved


def _normalize_polyglot_symbol(
    raw_symbol: Dict,
    content: str,
    language: str = "typescript",
    *,
    utf16_offset_map=None,
    normalization_profile_context=None,
    canonical_type_cache=None,
) -> Dict:
    """Normalize TS/JS and polyglot sequencer outputs into the Atlas symbol contract."""
    name = str(raw_symbol.get("name") or "anonymous")
    raw_type = str(raw_symbol.get("type") or raw_symbol.get("kind") or "Meta")
    type_map = {
        "Method": "Function",
        "Func": "Function",
        "Struct": "Class",
        "Enum": "Class",
        "Property": "Variable",
        "Package": "Meta",
        "Namespace": "Meta",
    }
    symbol_type = type_map.get(raw_type, raw_type)
    allowed_types = {
        "Variable",
        "Component",
        "Hook",
        "Arrow",
        "Class",
        "Function",
        "DefaultExport",
        "Interface",
        "TypeDefinition",
        "ReExportedSymbol",
        "ProxyExport",
        "LocalReExport",
        "Meta",
    }
    if symbol_type not in allowed_types:
        symbol_type = "Meta"

    location = raw_symbol.get("location") if isinstance(raw_symbol.get("location"), dict) else {}
    parser_kind_hint = str(
        raw_symbol.get("parserKind") or raw_symbol.get("parser_kind") or ""
    ).strip()
    compiler_offsets = (
        language in {"typescript", "javascript"}
        and parser_kind_hint == "typescript_compiler_api"
    )
    if compiler_offsets:
        raw_start = raw_symbol.get("start")
        raw_end = raw_symbol.get("end")
        if raw_start in (None, "") or raw_end in (None, ""):
            raise ValueError("TypeScript compiler symbol is missing start or end offset")
        try:
            start = int(raw_start)
            end = int(raw_end)
        except (TypeError, ValueError) as exc:
            raise ValueError("TypeScript compiler symbol has a non-integer offset") from exc
        if end < start:
            raise ValueError(
                f"TypeScript compiler symbol end precedes start: start={start}, end={end}"
            )
    else:
        start = raw_symbol.get("start", location.get("line", 1))
        end = raw_symbol.get("end", start)
        try:
            start = int(start or 1)
        except (TypeError, ValueError):
            start = 1
        try:
            end = int(end or start)
        except (TypeError, ValueError):
            end = start
        if end < start:
            end = start
    raw_line = raw_symbol.get("line", location.get("line"))
    raw_end_line = raw_symbol.get("endLine", raw_symbol.get("end_line", location.get("end_line")))
    raw_source_lines = raw_symbol.get("sourceLines", raw_symbol.get("source_lines", ""))
    if compiler_offsets:
        start = (
            utf16_offset_map[start]
            if utf16_offset_map is not None and start in utf16_offset_map
            else _utf16_offset_to_utf8_byte_offset(content, start)
        )
        end = (
            utf16_offset_map[end]
            if utf16_offset_map is not None and end in utf16_offset_map
            else _utf16_offset_to_utf8_byte_offset(content, end)
        )
    try:
        line = int(raw_line if raw_line not in (None, "") else start)
    except (TypeError, ValueError):
        line = start
    try:
        end_line = int(raw_end_line if raw_end_line not in (None, "") else end)
    except (TypeError, ValueError):
        end_line = end
    if line <= 0:
        line = start
    if end_line < line:
        end_line = max(line, end)
    source_lines = str(raw_source_lines or f"L{line}-L{end_line}")

    signature = str(raw_symbol.get("signature") or raw_symbol.get("detail") or name)
    dna = str(raw_symbol.get("dna") or "")
    if not dna:
        dna = hashlib.sha1(f"{name}:{symbol_type}:{start}:{signature}".encode("utf-8")).hexdigest()[:16]
    logic_dna = str(raw_symbol.get("logicDna") or raw_symbol.get("logic_dna") or "")
    if not logic_dna:
        logic_dna = hashlib.sha256(f"{name}:{symbol_type}:{signature}".encode("utf-8")).hexdigest()
    semantic_signature = str(raw_symbol.get("semanticSignature") or raw_symbol.get("semantic_signature") or signature)
    canonical_type_hint = (
        raw_symbol.get("canonicalSymbolType")
        or raw_symbol.get("canonical_symbol_type")
    )
    if canonical_type_hint:
        canonical_type = str(canonical_type_hint)
    elif canonical_type_cache is not None:
        canonical_cache_key = (language, raw_type)
        if canonical_cache_key not in canonical_type_cache:
            canonical_type_cache[canonical_cache_key] = canonical_symbol_type(
                raw_type,
                language=language,
            )
        canonical_type = str(canonical_type_cache[canonical_cache_key])
    else:
        canonical_type = str(canonical_symbol_type(raw_type, language=language))
    framework_tags = raw_symbol.get("frameworkTags", raw_symbol.get("framework_tags", []))
    if not isinstance(framework_tags, list):
        framework_tags = []
    framework_tags = [str(tag).strip() for tag in framework_tags if isinstance(tag, str) and str(tag).strip()]
    if normalization_profile_context is None:
        default_profile_name, default_profile = normalization_profile_for_language(language)
    else:
        default_profile_name, default_profile = normalization_profile_context
    normalization_profile = str(
        raw_symbol.get("normalizationProfile")
        or raw_symbol.get("normalization_profile")
        or default_profile_name
    )
    semantic_depth = str(raw_symbol.get("semanticDepth") or raw_symbol.get("semantic_depth") or default_profile.get("semantic_depth") or "unavailable")
    logic_dna_kind = str(raw_symbol.get("logicDnaKind") or raw_symbol.get("logic_dna_kind") or default_profile.get("logic_dna_kind") or "unavailable")
    normalization_confidence = str(raw_symbol.get("normalizationConfidence") or raw_symbol.get("normalization_confidence") or default_profile.get("normalization_confidence") or "none")
    parser_kind = str(raw_symbol.get("parserKind") or raw_symbol.get("parser_kind") or default_profile.get("parser_kind") or "unknown")
    parser_version = str(raw_symbol.get("parserVersion") or raw_symbol.get("parser_version") or default_profile.get("parser_version") or "unknown")

    dependencies = raw_symbol.get("dependencies")
    if not isinstance(dependencies, list):
        dependencies = raw_symbol.get("symbols_referenced", [])
    if not isinstance(dependencies, list):
        dependencies = []
    features = raw_symbol.get("features", [])
    if not isinstance(features, list):
        features = []

    return {
        "name": name,
        "type": symbol_type,
        "signature": signature,
        "dna": dna,
        "dna_short": dna[:16],
        "logic_dna": logic_dna,
        "logic_dna_short": logic_dna[:16],
        "semantic_signature": semantic_signature,
        "canonical_symbol_type": canonical_type,
        "framework_tags": framework_tags,
        "normalization_profile": normalization_profile,
        "semantic_depth": semantic_depth,
        "logic_dna_kind": logic_dna_kind,
        "normalization_confidence": normalization_confidence,
        "parser_kind": parser_kind,
        "parser_version": parser_version,
        "start": start,
        "end": end,
        "line": line,
        "end_line": end_line,
        "source_lines": source_lines,
        "dependencies": dependencies,
        "features": features,
        "exported": bool(raw_symbol.get("exported", not name.startswith("_"))),
        "export_kind": raw_symbol.get("exportKind", raw_symbol.get("export_kind", "local")),
        "modifiers": raw_symbol.get("modifiers", []) if isinstance(raw_symbol.get("modifiers", []), list) else [],
        "extends": raw_symbol.get("extends", []) if isinstance(raw_symbol.get("extends", []), list) else [],
        "implements": raw_symbol.get("implements", []) if isinstance(raw_symbol.get("implements", []), list) else [],
        "members": raw_symbol.get("members", []) if isinstance(raw_symbol.get("members", []), list) else [],
        "member_details": raw_symbol.get("memberDetails", raw_symbol.get("member_details", [])),
        "module_specifier": raw_symbol.get("moduleSpecifier", raw_symbol.get("module_specifier", "")),
        "exported_names": raw_symbol.get("exportedNames", raw_symbol.get("exported_names", [])),
        "dependency_imports": raw_symbol.get("dependencyImports", raw_symbol.get("dependency_imports", [])),
        "dynamic_imports": raw_symbol.get("dynamicImports", raw_symbol.get("dynamic_imports", [])),
        "ui_dependencies": raw_symbol.get("uiDependencies", raw_symbol.get("ui_dependencies", [])),
        "architectural_markers": raw_symbol.get("architecturalMarkers", raw_symbol.get("architectural_markers", [])),
        "runtime_contract": bool(raw_symbol.get("runtimeContract", raw_symbol.get("runtime_contract", False))),
        "runtime_contract_kind": str(raw_symbol.get("contractKind", raw_symbol.get("runtime_contract_kind", "")) or ""),
        "state_flow": summarize_state_flow_features(features),
    }


def _normalize_polyglot_symbols(
    raw_symbols: List[Dict],
    content: str,
    language: str,
    *,
    normalization_profile_context=None,
    canonical_type_cache=None,
) -> List[Dict]:
    """Normalize a file's symbols without rescanning the source for every offset."""

    if not raw_symbols:
        return []
    requested_offsets = []
    if language in {"typescript", "javascript"}:
        for raw_symbol in raw_symbols:
            if not isinstance(raw_symbol, dict):
                continue
            parser_kind = str(
                raw_symbol.get("parserKind") or raw_symbol.get("parser_kind") or ""
            ).strip()
            if parser_kind != "typescript_compiler_api":
                continue
            for field in ("start", "end"):
                raw_offset = raw_symbol.get(field)
                try:
                    requested_offsets.append(int(raw_offset))
                except (TypeError, ValueError):
                    # Preserve the canonical per-symbol validation error.
                    continue

    offset_map = _utf16_offsets_to_utf8_byte_offsets(content, requested_offsets)
    if normalization_profile_context is None:
        normalization_profile_context = normalization_profile_for_language(language)
    if canonical_type_cache is None:
        canonical_type_cache = {}
    return [
        _normalize_polyglot_symbol(
            raw_symbol,
            content,
            language=language,
            utf16_offset_map=offset_map,
            normalization_profile_context=normalization_profile_context,
            canonical_type_cache=canonical_type_cache,
        )
        for raw_symbol in raw_symbols
    ]


def _symbol_occurrence_dependencies(symbol: Dict) -> List[str]:
    """Return only dependencies owned by one compact symbol occurrence."""

    dependencies = symbol.get("dependencies", [])
    if not isinstance(dependencies, list):
        dependencies = []
    normalized = []
    for dependency in dependencies:
        value = str(dependency or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    module_specifier = str(symbol.get("module_specifier") or "").strip()
    if module_specifier and module_specifier not in normalized:
        normalized.append(module_specifier)
    return normalized


def _build_project_symbol_occurrences(files: Dict[str, Dict]) -> List[Dict]:
    """Build the collision-preserving project index without file-level fanout."""

    occurrences = []
    for rel_path, file_data in files.items():
        if not isinstance(file_data, dict):
            continue
        for symbol in file_data.get("symbols", []) or []:
            if not isinstance(symbol, dict):
                continue
            occurrence = {
                "name": symbol.get("name"),
                "type": symbol.get("type"),
                "file": rel_path,
                "dependencies": _symbol_occurrence_dependencies(symbol),
            }
            for coordinate in ("line", "char", "end_line", "source_lines"):
                if symbol.get(coordinate) is not None:
                    occurrence[coordinate] = symbol[coordinate]
            occurrences.append(occurrence)
    return occurrences


def _atlas_text_hash(content: str) -> str:
    """Return the canonical source hash stored in Atlas file records."""

    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _live_atlas_text_hash(path: str) -> str:
    """Hash live source with the same decoding contract used by Atlas enrichment."""

    with open(path, "r", encoding="utf-8", errors="ignore", newline="") as source_file:
        return _atlas_text_hash(source_file.read())


def _parser_strategy_for_language(language: str) -> str:
    return {
        "typescript": "node-ast",
        "javascript": "node-ast",
        "python": "python-ast",
        "java": "java-regex",
        "csharp": "cs-regex",
        "go": "go-regex",
    }.get(str(language or "").strip().lower(), "unavailable")


def _file_parser_evidence(raw_results, language: str) -> Dict[str, object]:
    """Project stable parser evidence owned by one source file."""

    reported = isinstance(raw_results, list)
    meta = next(
        (
            item
            for item in raw_results or []
            if isinstance(item, dict) and item.get("name") == "__file_meta__"
        ),
        {},
    )
    status = str(meta.get("parserStatus") or meta.get("parser_status") or "").strip().lower()
    if status not in {"observed", "degraded", "unavailable"}:
        status = "degraded" if reported else "unavailable"
    return {
        "strategy": _parser_strategy_for_language(language),
        "status": status,
        "reported_by_adapter": reported,
        "parser_kind": str(
            meta.get("parserKind") or meta.get("parser_kind") or "unavailable"
        ),
        "semantic_depth": str(
            meta.get("semanticDepth") or meta.get("semantic_depth") or "unavailable"
        ),
        "parser_diagnostic_count": int(
            meta.get("parserDiagnosticCount") or meta.get("parser_diagnostic_count") or 0
        ),
        "error_family": meta.get("errorFamily", meta.get("error_family")),
        "error_type": meta.get("errorType", meta.get("error_type")),
    }


def _build_project_sequencer_evidence(files: Dict[str, Dict]) -> Dict[str, object]:
    """Aggregate canonical parser coverage independently of fresh/resumed execution."""

    grouped: Dict[str, List[Dict[str, object]]] = {}
    for rel_path in sorted(files):
        file_data = files.get(rel_path)
        if not isinstance(file_data, dict):
            continue
        evidence = file_data.get("parser_evidence")
        if not isinstance(evidence, dict):
            evidence = _file_parser_evidence(
                None,
                str(file_data.get("language") or language_for_extension(Path(rel_path).suffix.lower())),
            )
        strategy = str(evidence.get("strategy") or "unavailable")
        grouped.setdefault(strategy, []).append({"path": rel_path, **evidence})

    details = []
    warnings = []
    for strategy in sorted(grouped):
        rows = grouped[strategy]
        status_counts = {
            status: sum(1 for row in rows if row.get("status") == status)
            for status in ("observed", "degraded", "unavailable")
        }
        non_observed = [row for row in rows if row.get("status") != "observed"]
        if status_counts["unavailable"]:
            claim_status = "unavailable"
        elif status_counts["degraded"]:
            claim_status = "degraded"
        else:
            claim_status = "proven"
        detail = {
            "strategy": strategy,
            "files_requested": len(rows),
            "files_reported_by_adapter": sum(
                1 for row in rows if row.get("reported_by_adapter") is True
            ),
            "files_accounted": len(rows),
            "status_counts": status_counts,
            "claim_status": claim_status,
        }
        if non_observed:
            samples = [
                {
                    "path": row["path"],
                    "status": row.get("status"),
                    "parser_kind": row.get("parser_kind"),
                    "semantic_depth": row.get("semantic_depth"),
                    "parser_diagnostic_count": row.get("parser_diagnostic_count", 0),
                    "error_family": row.get("error_family"),
                    "error_type": row.get("error_type"),
                }
                for row in non_observed[:20]
            ]
            if strategy == "node-ast":
                sample_key = "non_observed_file_samples"
                warning = (
                    "Node AST coverage is not fully observed; inspect status_counts and "
                    "non_observed_file_samples before making scope-wide claims."
                )
            elif strategy == "python-ast":
                sample_key = "degraded_file_samples"
                warning = (
                    "Python AST coverage is incomplete; inspect status_counts and "
                    "degraded_file_samples before making repository-wide structural claims."
                )
            else:
                sample_key = "unavailable_file_samples"
                warning = (
                    f"{strategy} structural coverage is incomplete; inspect status_counts and "
                    "unavailable_file_samples before making scope-wide claims."
                )
            detail[sample_key] = samples
            detail["warning"] = warning
            warnings.append({"strategy": strategy, "warning": warning})
        details.append(detail)

    return {
        "scope": "materialized_project_files",
        "repository_wide_claim": False,
        "claim_boundary": (
            "Coverage is deterministically aggregated from source-bound per-file parser evidence. "
            "Execution worker, chunk and checkpoint-reuse telemetry is not canonical Atlas state."
        ),
        "coverage": {
            "strategy": "+".join(sorted(grouped)) if grouped else "none",
            "details": details,
            "warnings": warnings,
        },
    }


def load_previous_atlas() -> Dict[str, Dict]:
    p = RAW_DIR / "atlas.json"
    data = load_json_file(p, {})
    if isinstance(data, dict):
        return data
    return {}


def compute_file_fingerprint(path: str, size: int) -> str:
    """
    Fast content fingerprint for lift fallback.
    Reads only file edges (first/last chunk) + size marker to avoid full-file hashing.
    """
    if size < 0:
        return ""
    chunk = FINGERPRINT_CHUNK_SIZE
    try:
        with open(path, "rb") as f:
            head = f.read(chunk)
            if size > chunk:
                back_seek = max(0, size - chunk)
                f.seek(back_seek)
                tail = f.read(chunk)
            else:
                tail = b""
        digest = hashlib.sha1(head + b"::" + tail + f"::{size}".encode("utf-8")).hexdigest()
        return digest
    except Exception as exc:
        record_honesty_event(
            component="generate_atlas",
            category="caught_error",
            operation="compute_file_fingerprint",
            subject=str(path),
            reason="fast file fingerprint could not be computed",
            fallback="force_full_file_sequence",
            claim_impact="performance_only",
            exception=exc,
        )
        return ""


def generate_atlas(stale_projects=None, dry_run=False, surgical_files=None):
    """Generate atlas for ALL resolved projects. Returns (multi_atlas, changed_files, dna_changed_files)"""
    atlas_start = perf_counter()
    reset_path_resolution_caches()
    if stale_projects is not None:
         logger.info(f"[FAST] [CACHE] Atlas incremental mode: {stale_projects}")
    surgical_files_by_project = {}
    for item in surgical_files or []:
        text = str(item or "").replace("\\", "/").strip()
        if not text:
            continue
        if "::" in text:
            pkey, rel_path = text.split("::", 1)
        else:
            pkey, rel_path = "MAIN", text
        pkey = pkey.strip()
        rel_path = normalize_path(rel_path.strip().lstrip("/"))
        if pkey and rel_path:
            surgical_files_by_project.setdefault(pkey, set()).add(rel_path)
    if surgical_files_by_project:
        logger.info(
            "[FAST] [CACHE] Atlas surgical file scope: %s"
            % {pkey: len(paths) for pkey, paths in surgical_files_by_project.items()}
        )

    # [PHASE 9] Ensure directories exist
    ensure_output_dir()
    ensure_output_dir()

    # [PHASE 9] Architecture constants
    discovery = {}
    projects = resolve_runtime_projects(ROOT)
    ownership_exclusions = project_ownership_exclusions(projects)
    declared_topology = (
        DYNAMIC_CONFIG.get("_target_root_override")
        if isinstance(DYNAMIC_CONFIG.get("_target_root_override"), dict)
        else DYNAMIC_CONFIG.get("_repository_topology")
        if isinstance(DYNAMIC_CONFIG.get("_repository_topology"), dict)
        else {}
    )
    for project_key, relative_paths in (
        declared_topology.get("project_ownership_exclusions", {}) or {}
    ).items():
        if project_key not in projects or not isinstance(relative_paths, list):
            continue
        ownership_exclusions[project_key] = sorted({
            (ROOT / str(relative_path)).resolve()
            for relative_path in relative_paths
        })
    for project_key, relative_paths in surgical_files_by_project.items():
        project_root = projects.get(project_key)
        if project_root is None:
            raise ValueError(
                f"Surgical Atlas scope references unknown project: {project_key}"
            )
        invalid_paths = sorted(
            relative_path
            for relative_path in relative_paths
            if not is_project_owned_path(
                project_root,
                relative_path,
                excluded_roots=ownership_exclusions.get(project_key, []),
            )
        )
        if invalid_paths:
            raise ValueError(
                "Surgical Atlas scope crosses canonical project ownership: "
                f"{project_key}::{', '.join(invalid_paths)}"
            )
    expected_projects = set(projects.keys())
    import tools.core.config as runtime_config

    bounded_projection_requested = bool(surgical_files_by_project or runtime_config.PROJECT_FILTER)
    if previous_atlas_required_for_generation(
        stale_projects,
        expected_projects,
        bounded_projection=bounded_projection_requested,
    ):
        prev_atlas = load_previous_atlas()
    else:
        prev_atlas = {}
        logger.info(
            "[CACHE] Full runtime project scope is stale; rebuilding without materializing the previous Atlas document."
        )
    previous_atlas_commit = {}
    if surgical_files_by_project:
        previous_atlas_commit = load_atlas_commit(RAW_DIR)
        parent_checks = (
            validate_atlas_commit(prev_atlas, previous_atlas_commit)
            if isinstance(prev_atlas, dict)
            and prev_atlas
            and isinstance(previous_atlas_commit, dict)
            and previous_atlas_commit
            else []
        )
        parent_failures = [
            row.get("name")
            for row in parent_checks
            if isinstance(row, dict) and not row.get("passed")
        ]
        if not parent_checks or parent_failures:
            raise ValueError(
                "Surgical Atlas generation requires a valid committed parent snapshot; "
                f"failures={parent_failures or ['parent_snapshot_unavailable']}"
            )
    if surgical_files_by_project and isinstance(prev_atlas, dict):
        normalized_surgical_files_by_project = {}
        for pkey, paths in surgical_files_by_project.items():
            files = (prev_atlas.get(pkey, {}) or {}).get("files", {})
            normalized_paths = set()
            for rel_path in paths:
                rel_norm = str(rel_path or "").replace("\\", "/").strip("/")
                if isinstance(files, dict) and rel_norm in files:
                    normalized_paths.add(rel_norm)
                    continue
                matched = None
                if isinstance(files, dict):
                    for atlas_rel, file_meta in files.items():
                        if not isinstance(file_meta, dict):
                            continue
                        candidates = {
                            str(file_meta.get("workspace_rel") or "").replace("\\", "/").strip("/"),
                            str(file_meta.get("repo_relative_path") or "").replace("\\", "/").strip("/"),
                            str(file_meta.get("target_ref") or "").split("::", 1)[-1].replace("\\", "/").strip("/"),
                        }
                        if rel_norm in candidates:
                            matched = str(atlas_rel).replace("\\", "/").strip("/")
                            break
                normalized_paths.add(matched or rel_norm)
            normalized_surgical_files_by_project[pkey] = normalized_paths
        surgical_files_by_project = normalized_surgical_files_by_project
    predicted_themes = discovery.get("predicted_themes", [])
    predicted_themes_lower = [(theme, theme.lower()) for theme in predicted_themes]
    test_mappings = discovery.get("test_mappings", {})
    missing_cached_projects = sorted(expected_projects - set(prev_atlas.keys()))
    effective_stale_projects = None if stale_projects is None else sorted(set(stale_projects) | set(missing_cached_projects))

    if (
        surgical_files_by_project
        and isinstance(prev_atlas, dict)
        and cached_atlas_covers_projects(prev_atlas, expected_projects)
    ):
        changed_projects = set(surgical_files_by_project.keys())
        broad_stale_projects = set(stale_projects or [])
        unchanged_broad_stale = sorted((broad_stale_projects - changed_projects) & expected_projects)
        if unchanged_broad_stale:
            logger.warning(
                "[FAST] [WATCHDOG] Surgical Atlas scope will reuse %s unchanged stale projects from cache: %s. "
                "This preserves save-time latency and keeps repo-wide freshness as proof debt.",
                len(unchanged_broad_stale),
                unchanged_broad_stale,
            )
        effective_stale_projects = sorted(changed_projects | set(missing_cached_projects))

    if missing_cached_projects:
        logger.info(f"[CACHE] Atlas cache is partial; missing projects will be rebuilt: {missing_cached_projects}")

    if (
        effective_stale_projects is not None
        and len(effective_stale_projects) == 0
        and cached_atlas_covers_projects(prev_atlas, expected_projects)
    ):
        logger.info("[CACHE] No stale projects detected; lifting atlas directly from cache.")
        return prev_atlas, [], []
    
    # Global file cache for Tier 2 [pkey, rel_path] -> entry
    file_cache = {}
    for pkey, pdata in prev_atlas.items():
        if "files" in pdata:
            for rel, f_data in pdata["files"].items():
                file_cache[(pkey, rel)] = f_data

    staging_store = None
    staging_run_id = ""
    staging_producer_contract = ""
    staged_file_cache = {}
    staged_sequencer_cache = {}
    if not dry_run:
        from tools.core.artifact_store import STORE

        if STORE.use_sqlite:
            try:
                staging_store = STORE
                staging_producer_contract = atlas_staging_producer_contract()
                staging_run_id = staging_store.begin_atlas_staging_run(staging_producer_contract)
                staged_file_cache = staging_store.load_atlas_staging_files(
                    staging_producer_contract,
                    stage_kind="atlas_file",
                )
                staged_sequencer_cache = staging_store.load_atlas_staging_files(
                    staging_producer_contract,
                    stage_kind="sequencer_result",
                )
                logger.info(
                    "[ATLAS_STAGING] Resumable generation opened run=%s completed_files=%s sequencer_files=%s",
                    staging_run_id,
                    len(staged_file_cache),
                    len(staged_sequencer_cache),
                )
            except Exception as exc:
                raise AtlasStagingWriteError(
                    "SQLite Atlas staging could not be initialized; refusing a long non-resumable run."
                ) from exc

    def checkpoint_staging_reuse(reused_files: int, stage_label: str) -> None:
        if staging_store is None or not staging_run_id or reused_files <= 0:
            return
        try:
            staging_store.record_atlas_staging_reuse(staging_run_id, reused_files)
        except Exception as exc:
            raise AtlasStagingWriteError(
                f"Atlas staging reuse receipt failed during {stage_label}."
            ) from exc

    import_resolution_cache = {}
    path_exists_cache = {}

    def resolve_import(imp, current_file_dir, project_root):
        key = (imp, current_file_dir, project_root)
        if key not in import_resolution_cache:
            import_resolution_cache[key] = resolve_project_import(
                imp,
                current_file_dir,
                str(ROOT),
                str(project_root),
                alias_map=get_alias_map(current_file_dir, str(project_root)),
            )
        return import_resolution_cache[key]

    def path_exists(path):
        if path not in path_exists_cache:
            path_exists_cache[path] = os.path.exists(path)
        return path_exists_cache[path]

    def staged_source_identity_matches(staged, full_path, current_mtime, current_size, *, allow_fingerprint=True):
        """Accept provisional work only for the same bounded source identity."""
        if not isinstance(staged, dict) or current_size < 0 or staged.get("size") is None:
            return False
        try:
            size_matches = int(staged.get("size")) == int(current_size)
        except (TypeError, ValueError):
            return False
        if not size_matches:
            return False
        if staged.get("hash"):
            try:
                return _live_atlas_text_hash(full_path) == str(staged.get("hash"))
            except OSError:
                return False
        if _mtime_close(staged.get("mtime"), current_mtime):
            return True
        if not allow_fingerprint or not staged.get("fingerprint"):
            return False
        current_fingerprint = compute_file_fingerprint(full_path, int(current_size))
        return bool(current_fingerprint and current_fingerprint == staged.get("fingerprint"))

    def ensure_structure_path(structure: Dict, rel_path: str):
        parts = [part for part in str(rel_path or "").split("/") if part]
        if not parts:
            return
        node = structure
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault(parts[-1], {})

    def prune_structure_path(structure: Dict, rel_path: str) -> bool:
        """Remove one indexed leaf and only the parent nodes made empty by it."""
        parts = [part for part in str(rel_path or "").split("/") if part]
        if not parts or not isinstance(structure, dict):
            return False
        node = structure
        lineage: list[tuple[Dict, str]] = []
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                return False
            lineage.append((node, part))
            node = child
        if parts[-1] not in node:
            return False
        node.pop(parts[-1], None)
        for parent, key in reversed(lineage):
            child = parent.get(key)
            if not isinstance(child, dict) or child:
                break
            parent.pop(key, None)
        return True

    def get_file_type(path, project_root):
        """Dinamik dosya kategorizasyonu: Doktrindeki layer_heuristics'i kullanır."""
        from tools.core.config import DOCTRINE
        rel = os.path.relpath(path, project_root)
        parts = {p.lower() for p in rel.replace("\\", "/").split("/")}
        filename = os.path.basename(path).lower()
        
        heuristics = DOCTRINE.get("layer_heuristics", {})
        
        if filename.endswith(".tsx"):
            for trigger, label in heuristics.get("fsd", []):
                if trigger.lower() in parts:
                    return label.lower().rstrip('s')
            return "component"

        if filename.startswith("use") and filename.endswith((".ts", ".tsx")): return "hook"
        
        hex_rules = heuristics.get("hexagonal", [])
        for trigger, label in hex_rules:
            if trigger.lower() in parts:
                if "domain" in trigger: return "model"
                if "application" in trigger or "service" in trigger: return "service"
                if "infra" in trigger or "adapter" in trigger: return "api"
        
        if "types" in filename or "schema" in filename: return "model"
        if "utils" in filename or "helpers" in filename: return "util"
        return "other"

    def _resolve_ast_batch_strategy(file_count: int, atlas_project_workers: int):
        env_chunk_size = os.getenv("CODEMAPS_AST_CHUNK_SIZE", "").strip()
        env_workers = os.getenv("CODEMAPS_AST_BATCH_WORKERS", "").strip()
        force_legacy = os.getenv("CODEMAPS_AST_LEGACY_PARALLEL", "").strip().lower() in {"1", "true", "yes", "on"}
        allow_unsafe = os.getenv("CODEMAPS_ALLOW_UNSAFE_AST_PARALLEL", "").strip().lower() in {"1", "true", "yes"}
        strategy = ast_batch_strategy(
            file_count,
            atlas_project_workers,
            force_legacy=force_legacy,
            env_chunk_size=int(env_chunk_size) if env_chunk_size.isdigit() and int(env_chunk_size) > 0 else None,
            env_workers=int(env_workers) if env_workers.isdigit() and int(env_workers) > 0 else None,
            allow_unsafe=allow_unsafe,
        )
        if strategy.get("worker_override_clamped"):
            logger.warning(
                "[PROFILE] Requested CODEMAPS_AST_BATCH_WORKERS=%s exceeds safe cap=%s; clamping. "
                "Set CODEMAPS_ALLOW_UNSAFE_AST_PARALLEL=1 to bypass (not recommended).",
                env_workers,
                strategy.get("safe_worker_cap"),
            )
        if strategy.get("session_pool_memory_clamped"):
            logger.info(
                "[PROFILE] Node AST workers reduced to %s by host-memory cap=%s.",
                strategy.get("workers"),
                strategy.get("session_pool_memory_worker_cap"),
            )
        return strategy

    def _sequence_via_node_batch(file_paths: List[str], atlas_project_workers: int = 1, on_chunk=None):
        """Calls the Node.js high-fidelity sequencer in batches to avoid per-file process overhead."""
        import concurrent.futures
        import json
        import base64
        js_engine = CODE_MAPS_DIR / "tools" / "engines" / "ast_sequencer.cjs"
        if not file_paths:
            return {}, {"chunk_size": 0, "workers": 0, "chunk_jobs": 0, "strategy": "none"}
        strategy = _resolve_ast_batch_strategy(len(file_paths), atlas_project_workers)
        strategy["strategy"] = "node-ast"
        chunk_size = strategy["chunk_size"]
        requested_paths = {Path(path).resolve().as_posix() for path in file_paths}
        chunk_jobs = [
            (offset, file_paths[offset:offset + chunk_size])
            for offset in range(0, len(file_paths), chunk_size)
        ]
        max_workers = min(strategy["workers"], len(chunk_jobs))
        session_pool_override = os.getenv("CODEMAPS_AST_SESSION_POOL", "").strip().lower()
        if session_pool_override in {"0", "false", "no", "off"}:
            session_pool_enabled = False
        elif session_pool_override in {"1", "true", "yes", "on"}:
            session_pool_enabled = True
        else:
            session_pool_enabled = bool(strategy.get("session_pool_enabled", False))
        session_pool_enabled = (
            session_pool_enabled
            and len(file_paths) >= int(strategy.get("session_pool_min_files", 48) or 48)
        )
        strategy["session_pool_enabled"] = session_pool_enabled
        strategy["session_pool_override"] = session_pool_override or None
        strategy["chunk_jobs"] = len(chunk_jobs)
        strategy["workers_effective"] = max_workers
        strategy["session_pool_workers"] = max_workers if session_pool_enabled else 0

        worker_queue: queue.Queue[NodeAstWorkerSession] | None = None
        worker_sessions: list[NodeAstWorkerSession] = []
        if session_pool_enabled:
            from tools.core.config import CONFIG_FILE
            doctrine_file = CONFIG_FILE.parent / "architecture_doctrine.json"
            worker_cmd = [
                "node",
                str(js_engine),
                "--worker-jsonl",
                "--max-requests",
                str(int(strategy.get("session_worker_max_requests", 200) or 200)),
                "--max-rss-bytes",
                str(int(strategy.get("session_worker_max_rss_bytes", 805306368) or 805306368)),
            ]
            if doctrine_file.exists():
                worker_cmd.extend(["--doctrine-json", str(doctrine_file)])
            worker_queue = queue.Queue()
            for _ in range(max_workers):
                session = NodeAstWorkerSession(worker_cmd, cwd=CODE_MAPS_DIR, log=logger.info)
                worker_sessions.append(session)
                worker_queue.put(session)

        def normalize_batch_key(path_str: str) -> str:
            return Path(path_str).resolve().as_posix()

        def run_chunk(offset_and_chunk):
            offset, chunk = offset_and_chunk
            key_map = {}
            chunk_metrics = {
                "batch_process_starts": 0 if worker_queue is not None else 1,
                "fallback_process_starts": 0,
                "batch_failures": 0,
                "fallback_chunks": 0,
                "response_identity_failures": 0,
                "worker_restarts": 0,
                "request_replays": 0,
                "subprocess_duration_seconds": 0.0,
                "node_reported_rss_max_bytes": 0,
            }

            def payload_for(fpath):
                abs_path = Path(fpath).resolve()
                payload_path = abs_path.as_posix()
                key_map[payload_path] = payload_path
                return payload_path

            js_engine = CODE_MAPS_DIR / "tools" / "engines" / "ast_sequencer.cjs"
            from tools.core.config import CONFIG_FILE
            DOCTRINE_FILE = CONFIG_FILE.parent / "architecture_doctrine.json"

            cmd = ["node", str(js_engine), "--batch-json"]
            if DOCTRINE_FILE.exists():
                cmd.extend(["--doctrine-json", str(DOCTRINE_FILE)])
            
            for fpath in chunk:
                payload_path = payload_for(fpath)
                b64_path = base64.b64encode(payload_path.encode('utf-8')).decode('utf-8')
                cmd.extend(["--path-entry", b64_path])
            request_id = hashlib.sha256(
                ("\n".join(key_map) + f"\n{offset}").encode("utf-8")
            ).hexdigest()[:24]
            cmd.extend(["--batch-metrics", "--request-id", request_id])
            timeout_seconds = max(30, len(chunk) * 3)
            worker_session = None
            if worker_queue is not None:
                worker_session = worker_queue.get()
                request_started = perf_counter()
                try:
                    worker_stdout, worker_metrics = worker_session.request(
                        request_id=request_id,
                        paths=list(key_map),
                        timeout_seconds=timeout_seconds,
                    )
                    returncode = 0
                    stderr = ""
                    clean_stdout = worker_stdout.strip()
                    chunk_metrics["batch_process_starts"] += int(
                        worker_metrics.get("worker_process_starts", 0) or 0
                    )
                    chunk_metrics["worker_restarts"] += int(
                        worker_metrics.get("worker_restarts", 0) or 0
                    )
                    chunk_metrics["request_replays"] += int(
                        worker_metrics.get("request_replays", 0) or 0
                    )
                except NodeAstWorkerError as exc:
                    returncode = 1
                    stderr = str(exc)
                    clean_stdout = ""
                    chunk_metrics["batch_process_starts"] += int(
                        exc.metrics.get("worker_process_starts", 0) or 0
                    )
                    chunk_metrics["worker_restarts"] += int(
                        exc.metrics.get("worker_restarts", 0) or 0
                    )
                    chunk_metrics["request_replays"] += int(
                        exc.metrics.get("request_replays", 0) or 0
                    )
                finally:
                    duration = perf_counter() - request_started
            else:
                res, duration = run_observed_subprocess(
                    cmd,
                    cwd=CODE_MAPS_DIR,
                    label=f"atlas_node_batch_offset_{offset}",
                    timeout=timeout_seconds,
                    log=logger.info,
                )
                returncode = res.returncode
                stderr = res.stderr or ""
                clean_stdout = res.stdout.strip()
            chunk_metrics["subprocess_duration_seconds"] += float(duration or 0.0)
            raw_results = {}
            if returncode != 0:
                chunk_metrics["batch_failures"] += 1
                logger.debug(
                    "Node batch sequencing failed for chunk starting at %s after %.3fs, falling back to per-file mode: %s",
                    offset,
                    duration,
                    stderr.strip(),
                )
            else:
                if not clean_stdout:
                    clean_stdout = "{}"
                response_results, batch_meta, response_error = _decode_node_batch_response(
                    clean_stdout,
                    request_id=request_id,
                    expected_paths=set(key_map),
                )
                if response_error:
                    if worker_session is not None:
                        worker_session.restart()
                        chunk_metrics["worker_restarts"] += 1
                    chunk_metrics["batch_failures"] += 1
                    chunk_metrics["response_identity_failures"] += 1
                    logger.debug(
                        "Node batch response rejected for chunk starting at %s; "
                        "reason=%s expected request_id=%s files=%s",
                        offset,
                        response_error,
                        request_id,
                        len(chunk),
                    )
                else:
                    raw_results = response_results
                    chunk_metrics["node_reported_rss_max_bytes"] = int(
                        batch_meta.get("rssMaxObservedBytes", 0) or 0
                    )
            if worker_session is not None and worker_queue is not None:
                worker_queue.put(worker_session)
            normalized_results = {}
            for raw_key, value in raw_results.items():
                normalized_results[key_map.get(raw_key, normalize_batch_key(raw_key))] = value
            if normalized_results:
                return normalized_results, chunk_metrics

            # Fallback: recover per-file if batch output was empty/mismatched.
            chunk_metrics["fallback_chunks"] += 1
            for fpath in chunk:
                chunk_metrics["fallback_process_starts"] += 1
                payload_path = payload_for(fpath)
                single_cmd = ["node", str(js_engine)]
                from tools.core.config import CONFIG_FILE
                DOCTRINE_FILE = CONFIG_FILE.parent / "architecture_doctrine.json"
                if DOCTRINE_FILE.exists():
                    single_cmd.extend(["--doctrine-json", str(DOCTRINE_FILE)])
                single_cmd.append(payload_path)
                single, single_duration = run_observed_subprocess(
                    single_cmd,
                    cwd=CODE_MAPS_DIR,
                    label=f"atlas_node_single_{Path(fpath).name}",
                    timeout=timeout_seconds,
                    log=logger.info,
                )
                chunk_metrics["subprocess_duration_seconds"] += float(single_duration or 0.0)
                if single.returncode != 0 or not single.stdout.strip():
                    logger.debug(
                        "Node single-file sequencing skipped %s rc=%s duration_seconds=%.3f",
                        payload_path,
                        single.returncode,
                        single_duration,
                    )
                    continue
                normalized_results[key_map[payload_path]] = json.loads(single.stdout.strip())
            return normalized_results, chunk_metrics

        lifecycle_metrics = {
            "batch_process_starts": 0,
            "fallback_process_starts": 0,
            "batch_failures": 0,
            "fallback_chunks": 0,
            "response_identity_failures": 0,
            "worker_restarts": 0,
            "request_replays": 0,
            "subprocess_duration_seconds": 0.0,
            "node_reported_rss_max_bytes": 0,
            "checkpoint_callbacks": 0,
            "checkpoint_files": 0,
            "checkpoint_seconds": 0.0,
        }

        def accept_chunk(chunk_payload):
            chunk_result, chunk_metrics = chunk_payload
            for metric_name in (
                "batch_process_starts",
                "fallback_process_starts",
                "batch_failures",
                "fallback_chunks",
                "response_identity_failures",
                "worker_restarts",
                "request_replays",
            ):
                lifecycle_metrics[metric_name] += int(chunk_metrics.get(metric_name, 0) or 0)
            lifecycle_metrics["subprocess_duration_seconds"] += float(
                chunk_metrics.get("subprocess_duration_seconds", 0.0) or 0.0
            )
            lifecycle_metrics["node_reported_rss_max_bytes"] = max(
                lifecycle_metrics["node_reported_rss_max_bytes"],
                int(chunk_metrics.get("node_reported_rss_max_bytes", 0) or 0),
            )
            if on_chunk is not None and chunk_result:
                checkpoint_start = perf_counter()
                on_chunk(chunk_result)
                lifecycle_metrics["checkpoint_seconds"] += perf_counter() - checkpoint_start
                lifecycle_metrics["checkpoint_callbacks"] += 1
                lifecycle_metrics["checkpoint_files"] += len(chunk_result)
            return chunk_result

        try:
            if max_workers <= 1:
                results = {}
                for job in chunk_jobs:
                    chunk_result = accept_chunk(run_chunk(job))
                    results.update(chunk_result)
            else:
                results = {}
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    for chunk_payload in executor.map(run_chunk, chunk_jobs):
                        chunk_result = accept_chunk(chunk_payload)
                        results.update(chunk_result)
        except AtlasStagingWriteError:
            raise
        except Exception as e:
            logger.debug(f"Node batch sequencing failed: {str(e)}")
            strategy["workers_effective"] = 0
            strategy["error"] = str(e)
            results = {}
        finally:
            for session in worker_sessions:
                session.close()

        normalized_results = {
            normalize_batch_key(path): value
            for path, value in results.items()
            if normalize_batch_key(path) in requested_paths
        }
        status_counts = {"observed": 0, "degraded": 0, "unavailable": 0}
        non_observed = []
        for requested_path in sorted(requested_paths):
            symbols = normalized_results.get(requested_path)
            meta = next(
                (item for item in symbols or [] if isinstance(item, dict) and item.get("name") == "__file_meta__"),
                {},
            )
            status = str(meta.get("parserStatus") or "").strip().lower()
            if status not in status_counts:
                status = "unavailable" if symbols is None else "degraded"
            status_counts[status] += 1
            if status != "observed":
                non_observed.append({
                    "path": requested_path,
                    "status": status,
                    "parser_diagnostic_count": int(meta.get("parserDiagnosticCount", 0) or 0),
                })

        if status_counts["unavailable"]:
            claim_status = "unavailable"
        elif status_counts["degraded"]:
            claim_status = "degraded"
        else:
            claim_status = "proven"
        strategy.update({
            "files_requested": len(requested_paths),
            "files_reported_by_adapter": len(normalized_results),
            "files_accounted": sum(status_counts.values()),
            "status_counts": status_counts,
            "claim_status": claim_status,
            "non_observed_file_samples": non_observed[:20],
            "process_starts": (
                lifecycle_metrics["batch_process_starts"]
                + lifecycle_metrics["fallback_process_starts"]
            ),
            **{
                key: round(value, 6) if isinstance(value, float) else value
                for key, value in lifecycle_metrics.items()
            },
        })
        if non_observed:
            strategy["warning"] = (
                "Node AST coverage is not fully observed; inspect status_counts and "
                "non_observed_file_samples before making scope-wide claims."
            )
        return normalized_results, strategy

    def _sequence_via_python_batch(file_paths: List[str], project_name: str, project_root: str):
        """Sequence Python files and retain observed parser coverage."""
        if not file_paths:
            return {}, {"strategy": "none", "workers": 0}

        import json
        import sys

        results = {}
        requested_paths = {Path(path).resolve().as_posix() for path in file_paths}
        file_evidence = []
        batch_error = None
        try:
            cmd = [sys.executable, str(PYTHON_SEQUENCER), project_name, project_root]
            timeout_seconds = atlas_batch_sequencer_timeout_seconds()
            res, duration = run_observed_subprocess(
                cmd,
                cwd=CODE_MAPS_DIR,
                label=f"atlas_python_batch_{project_name}",
                timeout=timeout_seconds,
                log=logger.info,
            )
            if res.returncode == 0:
                raw = json.loads(res.stdout)
                project_data = raw.get(project_name, {})
                for rel_path, file_data in project_data.get("files", {}).items():
                    abs_path = (Path(project_root) / rel_path).resolve().as_posix()
                    if abs_path not in requested_paths:
                        continue
                    results[abs_path] = list(file_data.get("symbols", []) or []) + [{
                        "name": "__file_meta__",
                        "type": "Meta",
                        "parser_status": file_data.get("status", "unavailable"),
                        "parser_kind": file_data.get("parser_kind", "unavailable"),
                        "semantic_depth": file_data.get("semantic_depth", "unavailable"),
                        "error_family": file_data.get("error_family"),
                        "error_type": file_data.get("error_type"),
                        "features": [],
                    }]
                    file_evidence.append({
                        "path": rel_path,
                        "status": file_data.get("status", "unavailable"),
                        "parser_kind": file_data.get("parser_kind", "unavailable"),
                        "semantic_depth": file_data.get("semantic_depth", "unavailable"),
                        "error_family": file_data.get("error_family"),
                        "error_type": file_data.get("error_type"),
                    })
            else:
                logger.warning("Python batch sequencing failed rc=%s duration_seconds=%.3f: %s", res.returncode, duration, res.stderr)
                batch_error = f"subprocess_exit_{res.returncode}"
        except Exception as exc:
            logger.warning(f"Python batch sequencing error: {exc}")
            batch_error = type(exc).__name__

        reported_paths = {
            (Path(project_root) / item["path"]).resolve().as_posix()
            for item in file_evidence
        }
        adapter_reported_count = len(file_evidence)
        for missing_path in sorted(requested_paths - reported_paths):
            try:
                display_path = Path(missing_path).relative_to(Path(project_root).resolve()).as_posix()
            except ValueError:
                display_path = Path(missing_path).name
            file_evidence.append({
                "path": display_path,
                "status": "unavailable",
                "parser_kind": "unavailable",
                "semantic_depth": "unavailable",
                "error_family": batch_error or "missing_batch_result",
                "error_type": None,
            })

        status_counts = {
            status: sum(1 for item in file_evidence if item["status"] == status)
            for status in ("observed", "degraded", "unavailable")
        }
        degraded = [item for item in file_evidence if item["status"] != "observed"]
        claim_status = "proven" if file_evidence and not degraded else ("degraded" if file_evidence else "unavailable")
        strategy = "python-ast" if claim_status == "proven" else f"python-ast-{claim_status}"
        detail = {
            "strategy": strategy,
            "workers": 1,
            "files_requested": len(requested_paths),
            "files_reported_by_adapter": adapter_reported_count,
            "files_accounted": len(file_evidence),
            "status_counts": status_counts,
            "claim_status": claim_status,
            "degraded_file_samples": degraded[:20],
        }
        if degraded:
            detail["warning"] = (
                "Python AST coverage is incomplete; inspect status_counts and degraded_file_samples "
                "before making repository-wide structural claims."
            )
        return results, detail

    def _sequence_via_structural_batch(
        file_paths: List[str],
        project_name: str,
        project_root: str,
        *,
        sequencer_path: Path,
        strategy_name: str,
        telemetry_label: str,
    ):
        """Run one structural adapter under the shared parser-evidence contract."""
        if not file_paths:
            return {}, {"strategy": "none", "workers": 0}

        import json
        import sys

        results = {}
        requested_paths = {Path(path).resolve().as_posix() for path in file_paths}
        file_evidence = []
        batch_error = None
        try:
            cmd = [sys.executable, str(sequencer_path), project_name, project_root]
            res, duration = run_observed_subprocess(
                cmd,
                cwd=CODE_MAPS_DIR,
                label=f"atlas_{telemetry_label}_batch_{project_name}",
                timeout=atlas_batch_sequencer_timeout_seconds(),
                log=logger.info,
            )
            if res.returncode == 0:
                project_data = json.loads(res.stdout).get(project_name, {})
                for rel_path, file_data in project_data.get("files", {}).items():
                    abs_path = (Path(project_root) / rel_path).resolve().as_posix()
                    if abs_path not in requested_paths:
                        continue
                    results[abs_path] = list(file_data.get("symbols", []) or []) + [{
                        "name": "__file_meta__",
                        "type": "Meta",
                        "parser_status": file_data.get("status", "unavailable"),
                        "parser_kind": file_data.get("parser_kind", "unavailable"),
                        "semantic_depth": file_data.get("semantic_depth", "unavailable"),
                        "error_family": file_data.get("error_family"),
                        "error_type": file_data.get("error_type"),
                        "features": [],
                    }]
                    file_evidence.append({
                        "path": rel_path,
                        "status": file_data.get("status", "unavailable"),
                        "parser_kind": file_data.get("parser_kind", "unavailable"),
                        "semantic_depth": file_data.get("semantic_depth", "unavailable"),
                        "error_family": file_data.get("error_family"),
                        "error_type": file_data.get("error_type"),
                    })
            else:
                batch_error = f"subprocess_exit_{res.returncode}"
                logger.warning(
                    "%s batch sequencing failed rc=%s duration_seconds=%.3f: %s",
                    telemetry_label,
                    res.returncode,
                    duration,
                    res.stderr,
                )
        except Exception as exc:
            batch_error = type(exc).__name__
            logger.warning("%s batch sequencing error: %s", telemetry_label, exc)

        reported_paths = {
            (Path(project_root) / item["path"]).resolve().as_posix()
            for item in file_evidence
        }
        adapter_reported_count = len(file_evidence)
        for missing_path in sorted(requested_paths - reported_paths):
            try:
                display_path = Path(missing_path).relative_to(Path(project_root).resolve()).as_posix()
            except ValueError:
                display_path = Path(missing_path).name
            file_evidence.append({
                "path": display_path,
                "status": "unavailable",
                "parser_kind": "unavailable",
                "semantic_depth": "unavailable",
                "error_family": batch_error or "missing_batch_result",
                "error_type": None,
            })

        status_counts = {
            status: sum(1 for item in file_evidence if item["status"] == status)
            for status in ("observed", "unavailable")
        }
        unavailable = [item for item in file_evidence if item["status"] != "observed"]
        claim_status = "proven" if file_evidence and not unavailable else "unavailable"
        detail = {
            "strategy": strategy_name,
            "workers": 1,
            "files_requested": len(requested_paths),
            "files_reported_by_adapter": adapter_reported_count,
            "files_accounted": len(file_evidence),
            "status_counts": status_counts,
            "claim_status": claim_status,
            "unavailable_file_samples": unavailable[:20],
        }
        if unavailable:
            detail["warning"] = (
                f"{telemetry_label} structural coverage is incomplete; inspect status_counts "
                "and unavailable_file_samples before making scope-wide claims."
            )
        return results, detail

    def _sequence_via_java_batch(file_paths: List[str], project_name: str, project_root: str):
        return _sequence_via_structural_batch(
            file_paths, project_name, project_root,
            sequencer_path=JAVA_SEQUENCER, strategy_name="java-regex", telemetry_label="java",
        )

    def _sequence_via_cs_batch(file_paths: List[str], project_name: str, project_root: str):
        return _sequence_via_structural_batch(
            file_paths, project_name, project_root,
            sequencer_path=CS_SEQUENCER, strategy_name="cs-regex", telemetry_label="csharp",
        )

    def _sequence_via_go_batch(file_paths: List[str], project_name: str, project_root: str):
        return _sequence_via_structural_batch(
            file_paths, project_name, project_root,
            sequencer_path=GO_SEQUENCER, strategy_name="go-regex", telemetry_label="go",
        )


    def _load_file_contents(entries: List[Dict]) -> List[Dict]:
        import concurrent.futures

        def load_entry(entry: Dict):
            try:
                # Preserve CRLF exactly so TypeScript UTF-16 offsets and Python's
                # canonical byte-span projection refer to the same source text.
                with open(entry["full_path"], 'r', encoding='utf-8', errors='ignore', newline='') as f:
                    loaded = dict(entry)
                    loaded["content"] = f.read()
                    return loaded
            except Exception as exc:
                record_honesty_event(
                    component="generate_atlas",
                    category="caught_error",
                    operation="load_source_entry",
                    subject=str(entry.get("full_path")),
                    reason="source entry could not be loaded for Atlas sequencing",
                    fallback="skip_source_entry",
                    claim_impact="atlas_incomplete_until_rerun",
                    exception=exc,
                )
                return None

        if not entries:
            return []

        max_workers = min(16, max(1, (os.cpu_count() or 4) * 2), len(entries))
        if max_workers <= 1:
            return [loaded for loaded in (load_entry(entry) for entry in entries) if loaded]

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            return [loaded for loaded in executor.map(load_entry, entries) if loaded]

    def line_number_at_offset(line_breaks: List[int], offset: int) -> int:
        return bisect_left(line_breaks, max(0, int(offset))) + 1

    def atlas_project_worker_count(job_count: int, total_file_count: int = 0) -> int:
        env_value = os.getenv("CODEMAPS_ATLAS_PROJECT_WORKERS", "").strip()
        if env_value.isdigit():
            parsed = int(env_value)
            if parsed > 0:
                return min(parsed, job_count)
        return resolve_atlas_project_worker_count(job_count, total_files=total_file_count)

    def prune_walk_dirs(root: str, dirs: list[str], excluded_roots: set[Path]) -> None:
        prune_owned_walk_dirs(
            root,
            dirs,
            skipped_names=SKIP_DIRS,
            excluded_roots=excluded_roots,
        )

    def estimate_atlas_job_file_count(jobs) -> int:
        total = 0
        for _, pkey, ppath, surgical_scope in jobs:
            if surgical_scope:
                total += len(surgical_scope)
                continue
            cached_files = (
                (prev_atlas.get(pkey, {}) or {}).get("files", {})
                if isinstance(prev_atlas.get(pkey, {}), dict)
                else {}
            )
            if isinstance(cached_files, dict) and cached_files:
                total += len(cached_files)
                continue
            try:
                excluded_roots = set(ownership_exclusions.get(pkey, []))
                for root, dirs, files in os.walk(str(ppath)):
                    prune_walk_dirs(root, dirs, excluded_roots)
                    total += sum(1 for filename in files if Path(filename).suffix.lower() in AST_FILE_EXTENSIONS)
            except Exception as exc:
                record_honesty_event(
                    component="generate_atlas",
                    category="caught_error",
                    operation="estimate_atlas_job_file_count",
                    subject=str(ppath),
                    reason="Atlas project worker workload estimate failed",
                    fallback="single_project_worker",
                    claim_impact="atlas_parallelism_degraded",
                    exception=exc,
                )
        return total

    def build_project_atlas(index: int, pkey: str, ppath: Path, atlas_workers: int, surgical_rel_paths=None):
        surgical_rel_paths = set(surgical_rel_paths or [])
        surgical_mode = bool(surgical_rel_paths) and pkey in prev_atlas
        if surgical_mode:
            logger.info(f"[ATLAS] Surgically updating atlas for {pkey} ({len(surgical_rel_paths)} files)...")
        else:
            logger.info(f"[ATLAS] Building atlas for {pkey}...")
        project_start = perf_counter()
        project_root = ppath
        excluded_project_roots = set(ownership_exclusions.get(pkey, []))
        
        # Language detection
        if surgical_mode:
            prev_project = prev_atlas.get(pkey, {})
            prev_project_meta = prev_project.get("project", {}) if isinstance(prev_project, dict) else {}
            project_language = prev_project_meta.get("language", "unknown")
            project_framework = prev_project_meta.get("framework", "generic")
            project_contract_version = prev_project_meta.get("ast_contract_version", AST_CONTRACT_VERSION)
        else:
            language_hits = {language: False for language in extension_language_map().values()}
            for root, dirs, files in os.walk(str(project_root)):
                prune_walk_dirs(root, dirs, excluded_project_roots)
                for filename in files:
                    detected_language = language_for_extension(Path(filename).suffix.lower())
                    if detected_language in language_hits:
                        language_hits[detected_language] = True
            detected_languages = [name for name, present in language_hits.items() if present]
            is_polyglot = len(detected_languages) > 1
            project_language = "polyglot:" + ",".join(detected_languages) if is_polyglot else (detected_languages[0] if detected_languages else "unknown")
            project_framework = "React/Next.js/Vite" if language_hits.get("typescript") or language_hits.get("javascript") else "generic"
            project_contract_version = "polyglot-v1" if is_polyglot else (
                f"{project_language}-v1" if project_language in {"python", "java", "csharp", "go"} else AST_CONTRACT_VERSION
            )
        
        use_md5_lift_fallback = os.getenv("CODEMAPS_ATLAS_MD5_LIFT", "1").strip().lower() in {"1", "true", "yes", "on"}
        use_fingerprint_lift = os.getenv("CODEMAPS_ATLAS_FINGERPRINT_LIFT", "1").strip().lower() in {"1", "true", "yes", "on"}

        if surgical_mode:
            atlas = copy.deepcopy(prev_atlas.get(pkey, {}))
            if not isinstance(atlas, dict):
                atlas = {}
            atlas.setdefault("project", {})
            atlas["project"].update({
                "name": pkey,
                "language": project_language,
                "framework": project_framework,
                "root": str(project_root),
                "ast_contract_version": project_contract_version,
            })
            atlas.setdefault("structure", {})
            atlas.setdefault("files", {})
            atlas.setdefault("dependencies", {})
            atlas.setdefault("symbols", [])
            atlas.setdefault("features", {})
            atlas.setdefault("clusters", {})
            atlas.setdefault("public_contracts", {})
        else:
            atlas = {
                "project": {
                    "name": pkey,
                    "language": project_language,
                    "framework": project_framework,
                    "root": str(project_root),
                    "ast_contract_version": project_contract_version,
                },
                "structure": {},
                "files": {},
                "dependencies": {},
                "symbols": [],
                "features": {},
                "clusters": {},
                "public_contracts": {},
            }

        import_resolution_cache_local = {}
        path_exists_cache_local = {}

        def resolve_import_local(imp, current_file_dir, project_root_str, language="typescript"):
            key = (imp, current_file_dir, project_root_str, language)
            cached = import_resolution_cache_local.get(key)
            if cached is not None:
                return cached
            resolved = resolve_project_import(
                imp,
                current_file_dir,
                str(ROOT),
                project_root_str,
                alias_map=get_alias_map(current_file_dir, project_root_str),
                language=language
            )
            import_resolution_cache_local[key] = resolved
            return resolved

        def path_exists_local(path):
            cached = path_exists_cache_local.get(path)
            if cached is not None:
                return cached
            exists = os.path.exists(path)
            path_exists_cache_local[path] = exists
            return exists

        def parse_import_records_local(content, current_dir, project_root_str, language="typescript", raw_imports=None):
            records = []

            def add_record(source, name="*", kind="module"):
                resolved = resolve_import_local(source, current_dir, project_root_str, language=language)
                records.append({
                    "source": resolved,
                    "raw_source": source,
                    "name": name,
                    "kind": kind,
                })

            if language in {"typescript", "javascript", "vue"}:
                static_named = STATIC_NAMED_IMPORT_RE.findall(content)
                static_namespace = STATIC_NAMESPACE_IMPORT_RE.findall(content)
                reexport_named = REEXPORT_NAMED_RE.findall(content)
                dynamic_member = DYNAMIC_MEMBER_RE.findall(content)

                for names_blob, source in static_named + reexport_named:
                    for raw_name in names_blob.split(","):
                        candidate = raw_name.strip()
                        if not candidate:
                            continue
                        import_kind = "named"
                        if " as " in candidate:
                            imported_name = candidate.split(" as ", 1)[0].strip()
                        else:
                            imported_name = candidate
                        if imported_name.startswith("type "):
                            imported_name = imported_name[5:].strip()
                            import_kind = "type"
                        if imported_name:
                            add_record(source, imported_name, import_kind)

                for alias_name, source in static_namespace:
                    alias = str(alias_name or "").strip()
                    if alias:
                        add_record(source, alias, "namespace")

                for source, _, member_name in dynamic_member:
                    add_record(source, member_name, "dynamic-member")

                for source in _raw_import_sources_not_in_records(raw_imports, records):
                    add_record(source, "*", "module")
            elif language == "go":
                qualified_sources = set()
                for record in extract_go_qualified_imports(content):
                    source = str(record.get("source") or "")
                    name = str(record.get("name") or "")
                    if source and name:
                        add_record(source, name, "qualified-member")
                        qualified_sources.add(source)
                for source in raw_imports or []:
                    if source not in qualified_sources:
                        add_record(source)
            else:
                for source in raw_imports or []:
                    add_record(source)

            deduped = []
            seen = set()
            for record in records:
                key = (record["source"], record.get("raw_source", ""), record["name"], record["kind"])
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(record)
            return deduped

        def merge_import_records(base_records, extra_records):
            merged = []
            seen = set()
            for record in list(base_records or []) + list(extra_records or []):
                if not isinstance(record, dict):
                    continue
                key = (
                    record.get("source", ""),
                    record.get("raw_source", ""),
                    record.get("name", ""),
                    record.get("kind", ""),
                    record.get("scope", ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(record)
            return merged

        def python_import_records_from_symbols(ast_symbols, current_dir, project_root_str):
            records = []

            def add_record(raw_source, name="*", kind="module", scope="top_level"):
                source_text = str(raw_source or "").replace(".", "/")
                resolved = resolve_import_local(source_text, current_dir, project_root_str, language="python")
                records.append({
                    "source": resolved,
                    "raw_source": source_text,
                    "name": name,
                    "kind": kind,
                    "scope": scope,
                })

            for sym in ast_symbols or []:
                signature = str(sym.get("semantic_signature") or sym.get("signature") or "")
                if not signature.startswith("import:"):
                    continue
                features = sym.get("features", [])
                if not isinstance(features, list):
                    features = []
                scope = "local" if "import_scope:local" in features else "top_level"
                parts = signature.split(":")
                if len(parts) >= 3:
                    module = parts[1]
                    imported = parts[2]
                    if module and imported:
                        add_record(module, str(imported).strip(), "named", scope)
                elif len(parts) == 2:
                    module = parts[1]
                    if module:
                        add_record(module, "*", "module", scope)

            return merge_import_records([], records)

        pending_entries = []
        pending_ast_files = []
        project_changed_files = []
        project_dna_changed_files = []
        lifted_by_staging = 0
        lifted_sequencer_results = 0
        staging_files_persisted = 0
        staging_files_partitioned = 0
        staging_files_skipped_oversize = 0
        staging_sequencer_results_partitioned = 0
        staging_file_batch = []
        lifted_by_mtime = 0
        lifted_by_fingerprint = 0
        lifted_by_hash = 0
        walk_start = perf_counter()

        if surgical_mode:
            walk_items = []
            for rel_path in sorted(surgical_rel_paths):
                full_path = os.path.join(str(project_root), rel_path.replace("/", os.sep))
                walk_items.append((full_path, os.path.basename(full_path), rel_path))
        else:
            walk_items = []
            for root, dirs, files in os.walk(str(project_root)):
                prune_walk_dirs(root, dirs, excluded_project_roots)
                for file in sorted(files):
                    full_path = os.path.join(root, file)
                    rel_path = normalize_path(os.path.relpath(full_path, str(project_root)))
                    if not is_project_owned_path(
                        project_root,
                        rel_path,
                        excluded_roots=excluded_project_roots,
                    ):
                        continue
                    walk_items.append((full_path, file, rel_path))

        package_manifest_paths = [Path(full_path) for full_path, file, _ in walk_items if file == "package.json"]
        package_contract_changed = any(file == "package.json" for _, file, _ in walk_items)
        if surgical_mode and package_contract_changed:
            package_manifest_paths = []
            for manifest_root, manifest_dirs, manifest_files in os.walk(str(project_root)):
                prune_walk_dirs(manifest_root, manifest_dirs, excluded_project_roots)
                if "package.json" in manifest_files:
                    manifest_path = Path(manifest_root) / "package.json"
                    manifest_rel = normalize_path(os.path.relpath(manifest_path, str(project_root)))
                    if is_project_owned_path(
                        project_root,
                        manifest_rel,
                        excluded_roots=excluded_project_roots,
                    ):
                        package_manifest_paths.append(manifest_path)
        if not surgical_mode or package_contract_changed:
            atlas["public_contracts"] = build_package_public_contracts(project_root, package_manifest_paths)

        for full_path, file, rel_path in walk_items:
                if surgical_mode and not os.path.exists(full_path):
                    if rel_path in atlas.get("files", {}):
                        atlas["files"].pop(rel_path, None)
                        atlas.get("dependencies", {}).pop(rel_path, None)
                        prune_structure_path(atlas.get("structure", {}), rel_path)
                        project_changed_files.append(scoped_change_ref(pkey, rel_path))
                        project_dna_changed_files.append(scoped_change_ref(pkey, rel_path))
                    continue
                if file.endswith(STRUCTURE_FILE_EXTENSIONS):
                    ensure_structure_path(atlas["structure"], rel_path)
                if not is_analysis_source_file(rel_path):
                    continue

                try:
                    f_stat = os.stat(full_path)
                    f_mtime = f_stat.st_mtime
                    f_size = f_stat.st_size
                except OSError as exc:
                    record_honesty_event(
                        component="generate_atlas",
                        category="caught_error",
                        operation="stat_source_file",
                        subject=str(full_path),
                        reason="source file metadata unavailable during Atlas scan",
                        fallback="use_negative_mtime_and_size",
                        claim_impact="atlas_cache_lift_degraded",
                        exception=exc,
                    )
                    f_mtime = -1
                    f_size = -1

                staged = staged_file_cache.get((pkey, rel_path))
                if staged and file_contract_is_current(staged) and not surgical_mode:
                    if staged_source_identity_matches(
                        staged,
                        full_path,
                        f_mtime,
                        f_size,
                        allow_fingerprint=use_fingerprint_lift,
                    ):
                        lifted_staged = dict(staged)
                        lifted_staged["mtime"] = f_mtime
                        lifted_staged["size"] = f_size
                        atlas["files"][rel_path] = lifted_staged
                        atlas["dependencies"][rel_path] = lifted_staged.get("internal_deps", [])
                        project_changed_files.append(scoped_change_ref(pkey, rel_path))
                        canonical_cached = file_cache.get((pkey, rel_path))
                        previous_dna = canonical_cached.get("dna", "") if canonical_cached else ""
                        if lifted_staged.get("dna", "") != previous_dna:
                            project_dna_changed_files.append(scoped_change_ref(pkey, rel_path))
                        lifted_by_staging += 1
                        continue

                cached = file_cache.get((pkey, rel_path))
                if cached and file_contract_is_current(cached) and not surgical_mode:
                    prev_mtime = cached.get("mtime")
                    prev_size = cached.get("size")
                    should_lift = False

                    if _mtime_close(prev_mtime, f_mtime):
                        should_lift = True
                        lifted_by_mtime += 1
                    elif (
                        use_fingerprint_lift
                        and f_size >= 0
                        and prev_size is not None
                        and int(prev_size) == int(f_size)
                        and cached.get("fingerprint")
                    ):
                        current_fingerprint = compute_file_fingerprint(full_path, f_size)
                        if current_fingerprint and current_fingerprint == cached.get("fingerprint"):
                            lifted_cached = dict(cached)
                            lifted_cached["mtime"] = f_mtime
                            lifted_cached["size"] = f_size
                            lifted_cached["fingerprint"] = current_fingerprint
                            atlas["files"][rel_path] = lifted_cached
                            atlas["dependencies"][rel_path] = lifted_cached.get("internal_deps", [])
                            lifted_by_fingerprint += 1
                            continue
                    elif (
                        use_md5_lift_fallback
                        and f_size >= 0
                        and prev_size is not None
                        and int(prev_size) == int(f_size)
                        and cached.get("hash")
                    ):
                        try:
                            with open(full_path, "rb") as f_bin:
                                current_hash = hashlib.md5(f_bin.read()).hexdigest()
                        except Exception as exc:
                            record_honesty_event(
                                component="generate_atlas",
                                category="caught_error",
                                operation="md5_lift_fallback",
                                subject=str(full_path),
                                reason="MD5 lift fallback could not read source bytes",
                                fallback="full_file_sequence",
                                claim_impact="performance_only",
                                exception=exc,
                            )
                            current_hash = None
                        if current_hash and current_hash == cached.get("hash"):
                            lifted_cached = dict(cached)
                            lifted_cached["mtime"] = f_mtime
                            lifted_cached["size"] = f_size
                            atlas["files"][rel_path] = lifted_cached
                            atlas["dependencies"][rel_path] = lifted_cached.get("internal_deps", [])
                            lifted_by_hash += 1
                            continue

                    if should_lift:
                        atlas["files"][rel_path] = cached
                        atlas["dependencies"][rel_path] = cached.get("internal_deps", [])
                        continue

                pending_entries.append({
                    "full_path": full_path,
                    "rel_path": rel_path,
                    "project_root": str(project_root),
                    "current_dir": os.path.dirname(full_path),
                    "mtime": f_mtime,
                    "size": f_size,
                })
                pending_ast_files.append(full_path)
        walk_elapsed = perf_counter() - walk_start
        checkpoint_staging_reuse(lifted_by_staging, f"{pkey} completed-file lift")

        content_start = perf_counter()
        pending_entries = _load_file_contents(pending_entries)
        content_elapsed = perf_counter() - content_start

        ast_start = perf_counter()
        pending_entry_by_abs = {
            Path(entry["full_path"]).resolve().as_posix(): entry
            for entry in pending_entries
        }
        node_results_by_file = {}
        for abs_path, entry in pending_entry_by_abs.items():
            staged_result = staged_sequencer_cache.get((pkey, entry["rel_path"]))
            if not isinstance(staged_result, dict):
                continue
            source_matches = staged_source_identity_matches(
                staged_result,
                entry["full_path"],
                entry.get("mtime", -1),
                entry.get("size", -1),
            )
            staged_symbols = staged_result.get("results")
            if source_matches and isinstance(staged_symbols, list) and _parser_evidence_is_reusable(
                _file_parser_evidence(staged_symbols, language_for_extension(Path(entry["rel_path"]).suffix.lower()))
            ):
                node_results_by_file[abs_path] = staged_symbols
                lifted_sequencer_results += 1
        checkpoint_staging_reuse(
            lifted_sequencer_results,
            f"{pkey} sequencer-result lift",
        )

        def checkpoint_sequencer_results(results_by_path):
            nonlocal staging_sequencer_results_partitioned
            if staging_store is None or not staging_run_id or not results_by_path:
                return
            rows = []
            for abs_path, result in results_by_path.items():
                entry = pending_entry_by_abs.get(Path(abs_path).resolve().as_posix())
                if entry is None or not isinstance(result, list):
                    continue
                rows.append((
                    entry["rel_path"],
                    {
                        "mtime": entry.get("mtime", -1),
                        "size": entry.get("size", -1),
                        "fingerprint": compute_file_fingerprint(entry["full_path"], int(entry.get("size", -1))),
                        "hash": _atlas_text_hash(str(entry.get("content") or "")),
                        "results": result,
                    },
                ))
            batch_size = max(1, int(staging_store.atlas_staging_batch_size))
            try:
                for offset in range(0, len(rows), batch_size):
                    checkpoint_profile = staging_store.save_atlas_staging_batch(
                        staging_run_id,
                        staging_producer_contract,
                        pkey,
                        rows[offset:offset + batch_size],
                        stage_kind="sequencer_result",
                    )
                    partitioned = int(checkpoint_profile.get("partitioned", 0) or 0)
                    staging_sequencer_results_partitioned += partitioned
                    if partitioned:
                        logger.info(
                            "[ATLAS_STAGING] Partitioned %s oversized sequencer checkpoint(s) in %s.",
                            partitioned,
                            pkey,
                        )
                    skipped = int(checkpoint_profile.get("skipped_oversize", 0) or 0)
                    if skipped:
                        logger.warning(
                            "[ATLAS_STAGING] %s sequencer results exceeded the per-file checkpoint bound in %s.",
                            skipped,
                            pkey,
                        )
            except Exception as exc:
                raise AtlasStagingWriteError(
                    f"Atlas sequencer checkpoint failed for project {pkey}."
                ) from exc

        # Polyglot Dispatch: Group pending files by extension
        files_by_ext = {}
        for f in pending_ast_files:
            if Path(f).resolve().as_posix() in node_results_by_file:
                continue
            ext = Path(f).suffix.lower()
            files_by_ext.setdefault(ext, []).append(f)

        strategies = []
        strategy_details = []
        
        # Dispatch to specialized sequencers
        python_files = [
            path
            for extension in extensions_for_language("python")
            for path in files_by_ext.get(extension, [])
        ]
        if python_files:
            res, strat = _sequence_via_python_batch(python_files, pkey, str(project_root))
            node_results_by_file.update(res)
            checkpoint_sequencer_results(res)
            strategies.append(strat["strategy"])
            strategy_details.append(strat)
            
        if ".java" in files_by_ext:
            res, strat = _sequence_via_java_batch(files_by_ext[".java"], pkey, str(project_root))
            node_results_by_file.update(res)
            checkpoint_sequencer_results(res)
            strategies.append(strat["strategy"])
            strategy_details.append(strat)
            
        if ".cs" in files_by_ext:
            res, strat = _sequence_via_cs_batch(files_by_ext[".cs"], pkey, str(project_root))
            node_results_by_file.update(res)
            checkpoint_sequencer_results(res)
            strategies.append(strat["strategy"])
            strategy_details.append(strat)
            
        if ".go" in files_by_ext:
            res, strat = _sequence_via_go_batch(files_by_ext[".go"], pkey, str(project_root))
            node_results_by_file.update(res)
            checkpoint_sequencer_results(res)
            strategies.append(strat["strategy"])
            strategy_details.append(strat)
            
        # Node dispatch follows the same registry that defines active language support.
        js_ts_files = [
            path
            for language in ("typescript", "javascript")
            for extension in extensions_for_language(language)
            for path in files_by_ext.get(extension, [])
        ]
        if js_ts_files:
            res, strat = _sequence_via_node_batch(
                js_ts_files,
                atlas_project_workers=atlas_workers,
                on_chunk=checkpoint_sequencer_results,
            )
            node_results_by_file.update(res)
            strategies.append(strat["strategy"])
            strategy_details.append(strat)
            
        ast_strategy = {
            "strategy": "+".join(strategies) if strategies else "none",
            "workers": atlas_workers,
            "details": strategy_details,
            "workers_effective": max(
                [int(detail.get("workers_effective", detail.get("workers", 0)) or 0) for detail in strategy_details if isinstance(detail, dict)]
                or [0]
            ),
            "chunk_size": max(
                [int(detail.get("chunk_size", 0) or 0) for detail in strategy_details if isinstance(detail, dict)]
                or [0]
            ),
            "chunk_jobs": sum(
                int(detail.get("chunk_jobs", 0) or 0) for detail in strategy_details if isinstance(detail, dict)
            ),
            "adaptive_mode": any(
                bool(detail.get("adaptive_mode", False)) for detail in strategy_details if isinstance(detail, dict)
            ),
            "warnings": [
                {"strategy": detail.get("strategy"), "warning": detail.get("warning")}
                for detail in strategy_details
                if isinstance(detail, dict) and detail.get("warning")
            ],
            "process_starts": sum(
                int(detail.get("process_starts", 0) or 0) for detail in strategy_details if isinstance(detail, dict)
            ),
            "batch_process_starts": sum(
                int(detail.get("batch_process_starts", 0) or 0) for detail in strategy_details if isinstance(detail, dict)
            ),
            "fallback_process_starts": sum(
                int(detail.get("fallback_process_starts", 0) or 0) for detail in strategy_details if isinstance(detail, dict)
            ),
            "batch_failures": sum(
                int(detail.get("batch_failures", 0) or 0) for detail in strategy_details if isinstance(detail, dict)
            ),
            "fallback_chunks": sum(
                int(detail.get("fallback_chunks", 0) or 0) for detail in strategy_details if isinstance(detail, dict)
            ),
            "response_identity_failures": sum(
                int(detail.get("response_identity_failures", 0) or 0) for detail in strategy_details if isinstance(detail, dict)
            ),
            "worker_restarts": sum(
                int(detail.get("worker_restarts", 0) or 0) for detail in strategy_details if isinstance(detail, dict)
            ),
            "request_replays": sum(
                int(detail.get("request_replays", 0) or 0) for detail in strategy_details if isinstance(detail, dict)
            ),
            "session_pool_workers": max(
                [int(detail.get("session_pool_workers", 0) or 0) for detail in strategy_details if isinstance(detail, dict)]
                or [0]
            ),
            "node_files_requested": sum(
                int(detail.get("files_requested", 0) or 0)
                for detail in strategy_details
                if isinstance(detail, dict) and detail.get("strategy") == "node-ast"
            ),
            "subprocess_duration_seconds": sum(
                float(detail.get("subprocess_duration_seconds", 0.0) or 0.0) for detail in strategy_details if isinstance(detail, dict)
            ),
            "node_reported_rss_max_bytes": max(
                [int(detail.get("node_reported_rss_max_bytes", 0) or 0) for detail in strategy_details if isinstance(detail, dict)]
                or [0]
            ),
            "checkpoint_callbacks": sum(
                int(detail.get("checkpoint_callbacks", 0) or 0) for detail in strategy_details if isinstance(detail, dict)
            ),
            "checkpoint_files": sum(
                int(detail.get("checkpoint_files", 0) or 0) for detail in strategy_details if isinstance(detail, dict)
            ),
            "checkpoint_seconds": sum(
                float(detail.get("checkpoint_seconds", 0.0) or 0.0) for detail in strategy_details if isinstance(detail, dict)
            ),
        }
        ast_elapsed = perf_counter() - ast_start

        def flush_staging_file_batch():
            nonlocal staging_files_persisted, staging_files_partitioned, staging_files_skipped_oversize
            if staging_store is None or not staging_run_id or not staging_file_batch:
                return
            try:
                result = staging_store.save_atlas_staging_batch(
                    staging_run_id,
                    staging_producer_contract,
                    pkey,
                    list(staging_file_batch),
                    stage_kind="atlas_file",
                )
            except Exception as exc:
                raise AtlasStagingWriteError(
                    f"Completed Atlas file checkpoint failed for project {pkey}."
                ) from exc
            staging_files_persisted += int(result.get("persisted", 0) or 0)
            staging_files_partitioned += int(result.get("partitioned", 0) or 0)
            staging_files_skipped_oversize += int(result.get("skipped_oversize", 0) or 0)
            if int(result.get("partitioned", 0) or 0):
                logger.info(
                    "[ATLAS_STAGING] Partitioned %s oversized completed-file checkpoint(s) in %s.",
                    int(result.get("partitioned", 0) or 0),
                    pkey,
                )
            if int(result.get("skipped_oversize", 0) or 0):
                logger.warning(
                    "[ATLAS_STAGING] %s completed Atlas files exceeded the per-file checkpoint bound in %s.",
                    int(result.get("skipped_oversize", 0) or 0),
                    pkey,
                )
            staging_file_batch.clear()

        project_normalization_profiles = {}
        project_canonical_type_caches = {}
        enrich_start = perf_counter()
        for entry in pending_entries:
            full_path = entry["full_path"]
            rel_path = entry["rel_path"]
            content = entry["content"]
            current_dir = entry["current_dir"]

            scan_content = _strip_comments_for_scan(content)

            # [Polyglot] Detect language context for resolution
            f_ext = Path(full_path).suffix.lower()
            f_lang = language_for_extension(f_ext)
            raw_imports = sorted(set(extract_polyglot_imports(scan_content, f_lang)))
            import_records = parse_import_records_local(
                scan_content,
                current_dir,
                str(project_root),
                language=f_lang,
                raw_imports=raw_imports,
            )
            resolved_imports = sorted(list(set([
                resolve_import_local(imp, current_dir, str(project_root), language=f_lang)
                for imp in raw_imports
                if not imp.startswith(("react", "vue", "lucide", "@tiptap", "@tanstack", "zod", "zustand", "framer-motion", "next"))
            ])))
            internal_deps = sorted(list(set([
                d for d in resolved_imports
                if d.startswith(("src/", "./", "../")) or path_exists_local(os.path.join(str(project_root), d.replace("/", os.sep)))
            ])))

            fixed_deps = []
            for d in internal_deps:
                if d.startswith("."):
                    fixed_deps.append(to_posix_path(os.path.relpath(os.path.normpath(os.path.join(current_dir, d)), str(project_root))))
                else:
                    fixed_deps.append(to_posix_path(d))
            internal_deps = sorted(list(set(fixed_deps)))

            raw_node_results = node_results_by_file.get(Path(full_path).resolve().as_posix(), [])
            ast_symbols = []
            ast_features = []
            line_breaks = [i for i, char in enumerate(content) if char == '\n']
            content_last_offset = max(0, len(content) - 1)
            source_line_count = max(1, count_source_lines(content))
            if raw_node_results and f_lang not in project_normalization_profiles:
                project_normalization_profiles[f_lang] = normalization_profile_for_language(f_lang)
            for sym_entry in _normalize_polyglot_symbols(
                raw_node_results,
                content,
                language=f_lang,
                normalization_profile_context=project_normalization_profiles.get(f_lang),
                canonical_type_cache=project_canonical_type_caches.setdefault(f_lang, {}),
            ):
                parser_line = int(sym_entry.get("line") or 0)
                parser_end_line = int(sym_entry.get("end_line") or 0)
                parser_source_lines = str(sym_entry.get("source_lines") or "").strip()
                if parser_line > 0 and parser_end_line >= parser_line:
                    start_line = min(source_line_count, parser_line)
                    end_line = min(source_line_count, parser_end_line)
                    sym_entry["line"] = start_line
                    sym_entry["end_line"] = max(start_line, end_line)
                    sym_entry["source_lines"] = parser_source_lines or f"L{sym_entry['line']}-L{sym_entry['end_line']}"
                else:
                    start_offset = min(content_last_offset, max(0, int(sym_entry.get("start", 0) or 0)))
                    start_line = min(source_line_count, line_number_at_offset(line_breaks, start_offset))
                    # Parser end offsets usually point just after the node. Convert
                    # the last covered non-whitespace byte to a line so trailing
                    # newlines do not inflate editor spans by one line.
                    raw_end = int(sym_entry.get("end", start_offset) or start_offset)
                    end_offset = min(content_last_offset, max(start_offset, raw_end - 1))
                    while end_offset > start_offset and content[end_offset].isspace():
                        end_offset -= 1
                    end_line = min(source_line_count, line_number_at_offset(line_breaks, end_offset))
                    sym_entry["line"] = start_line
                    sym_entry["end_line"] = max(start_line, end_line)
                    sym_entry["source_lines"] = f"L{sym_entry['line']}-L{sym_entry['end_line']}"
                ast_symbols.append(sym_entry)
                if sym_entry.get('features'):
                    ast_features.extend(sym_entry['features'])
            if f_lang in {"typescript", "javascript"}:
                syntax_imports = extract_typescript_import_evidence(raw_node_results)
                import_records = []
                for record in syntax_imports["records"]:
                    raw_source = record["source"]
                    import_records.append({
                        "source": resolve_import_local(
                            raw_source,
                            current_dir,
                            str(project_root),
                            language=f_lang,
                        ),
                        "raw_source": raw_source,
                        "name": record["name"],
                        "kind": record["kind"],
                        "scope": record["scope"],
                    })

                def _resolved_runtime_sources(raw_sources):
                    return sorted(set(
                        resolve_import_local(source, current_dir, str(project_root), language=f_lang)
                        for source in raw_sources
                        if not source.startswith(("react", "vue", "lucide", "@tiptap", "@tanstack", "zod", "zustand", "framer-motion", "next"))
                    ))

                resolved_imports = _resolved_runtime_sources(syntax_imports["eager_sources"])
                lazy_resolved_imports = _resolved_runtime_sources(syntax_imports["lazy_sources"])

                def _internalize_typescript_deps(sources):
                    deps = sorted(set(
                        source
                        for source in sources
                        if source.startswith(("src/", "./", "../"))
                        or path_exists_local(os.path.join(str(project_root), source.replace("/", os.sep)))
                    ))
                    return sorted(set(
                        to_posix_path(os.path.relpath(os.path.normpath(os.path.join(current_dir, source)), str(project_root)))
                        if source.startswith(".") else to_posix_path(source)
                        for source in deps
                    ))

                internal_deps = _internalize_typescript_deps(resolved_imports)
                lazy_internal_deps = _internalize_typescript_deps(lazy_resolved_imports)
            elif f_lang == "python":
                import_records = python_import_records_from_symbols(ast_symbols, current_dir, str(project_root))
                top_level_sources = {
                    str(record.get("source") or "")
                    for record in import_records
                    if record.get("scope", "top_level") == "top_level"
                }
                local_sources = {
                    str(record.get("source") or "")
                    for record in import_records
                    if record.get("scope") == "local"
                }
                resolved_imports = sorted(source for source in top_level_sources if source)
                lazy_resolved_imports = sorted(source for source in local_sources if source)

                def _internalize_python_deps(sources):
                    deps = sorted(list(set([
                        d for d in sources
                        if d.startswith(("src/", "./", "../")) or path_exists_local(os.path.join(str(project_root), d.replace("/", os.sep)))
                    ])))
                    fixed = []
                    for d in deps:
                        if d.startswith("."):
                            fixed.append(to_posix_path(os.path.relpath(os.path.normpath(os.path.join(current_dir, d)), str(project_root))))
                        else:
                            fixed.append(to_posix_path(d))
                    return sorted(list(set(fixed)))

                internal_deps = _internalize_python_deps(resolved_imports)
                lazy_internal_deps = _internalize_python_deps(lazy_resolved_imports)
            else:
                lazy_internal_deps = []
            ast_features = sorted(list(set(ast_features)))
            state_flow = summarize_state_flow_features(ast_features)

            all_exports = _extract_exports_for_language(scan_content, f_lang, ast_symbols)

            f_mtime = entry["mtime"]
            f_size = entry.get("size", -1)
            f_hash = ""
            for nr in raw_node_results:
                if nr.get('name') == '__file_meta__':
                    for feat in nr.get('features', []):
                        if feat.startswith('Hash:'):
                            f_hash = feat.split(':', 1)[1]
                        elif feat.startswith('MTime:'):
                            f_mtime = float(feat.split(':', 1)[1])
                    break

            if not f_hash:
                try:
                    f_hash = _atlas_text_hash(content)
                except (TypeError, UnicodeEncodeError) as exc:
                    record_honesty_event(
                        component="generate_atlas",
                        category="caught_error",
                        operation="hash_source_content",
                        subject=str(full_path),
                        reason="source content hash could not be computed",
                        fallback="use_err_hash_marker",
                        claim_impact="atlas_file_identity_degraded",
                        exception=exc,
                    )
                    f_hash = "err"
            f_fingerprint = compute_file_fingerprint(full_path, f_size)

            file_type = get_file_type(full_path, str(project_root))
            try:
                workspace_rel = str(Path(full_path).relative_to(ROOT)).replace("\\", "/")
            except ValueError as exc:
                record_honesty_event(
                    component="generate_atlas",
                    category="caught_error",
                    operation="workspace_relative_path",
                    subject=str(full_path),
                    reason="source file is outside configured workspace root",
                    fallback="os_path_relpath",
                    claim_impact="atlas_workspace_projection_degraded",
                    exception=exc,
                )
                workspace_rel = os.path.relpath(full_path, str(ROOT)).replace("\\", "/")

            test_link = test_mappings.get(workspace_rel)
            workspace_rel_lower = workspace_rel.lower()
            file_themes = [theme for theme, theme_lower in predicted_themes_lower if theme_lower in workspace_rel_lower]
            if not file_themes:
                file_themes = ["Shared Logic"]

            # [Polyglot] Semantic Bridge: Identify potential route calls (candidates)
            api_candidates = []
            if f_lang == "typescript":
                api_candidates = re.findall(r"['\"](/[a-zA-Z0-9_\-/]+)['\"]", content)
                api_candidates = sorted(list(set([c for c in api_candidates if "/" in c])))

            f_data = {
                "api_candidates": api_candidates,
                "type": file_type,
                "language": f_lang,
                "project_key": pkey,
                "atlas_rel_path": rel_path,
                "workspace_rel": workspace_rel,
                "repo_relative_path": workspace_rel,
                "target_ref": f"{pkey}::{workspace_rel}",
                "loc": count_source_lines(content),
                "ast_contract_version": AST_CONTRACT_VERSION,
                "exports": all_exports,
                "imports": resolved_imports,
                "import_records": import_records,
                "internal_deps": internal_deps,
                "lazy_internal_deps": lazy_internal_deps,
                "symbols": [s for s in ast_symbols if s['name'] != '__file_meta__'],
                "features": ast_features,
                "state_flow": state_flow,
                "parser_evidence": _file_parser_evidence(raw_node_results, f_lang),
                "dna": "".join([s['dna'] or "" for s in ast_symbols if s['name'] != '__file_meta__']),
                "mtime": f_mtime,
                "size": f_size,
                "hash": f_hash,
                "fingerprint": f_fingerprint,
                "test_link": test_link,
                "themes": file_themes
            }

            cached = file_cache.get((pkey, rel_path))
            prev_dna = cached.get("dna", "") if cached else ""
            if f_data["dna"] != prev_dna:
                project_dna_changed_files.append(scoped_change_ref(pkey, rel_path))

            atlas["dependencies"][rel_path] = internal_deps
            atlas["files"][rel_path] = f_data
            project_changed_files.append(scoped_change_ref(pkey, rel_path))
            if staging_store is not None and staging_run_id:
                staging_file_batch.append((rel_path, f_data))
                if len(staging_file_batch) >= max(1, int(staging_store.atlas_staging_batch_size)):
                    flush_staging_file_batch()
        flush_staging_file_batch()
        enrich_elapsed = perf_counter() - enrich_start

        index_start = perf_counter()
        atlas["project"]["sequencer_evidence"] = _build_project_sequencer_evidence(atlas["files"])
        atlas["features"] = {}
        atlas["clusters"] = {}
        atlas["symbols"] = _build_project_symbol_occurrences(atlas["files"])
        index_elapsed = perf_counter() - index_start

        cluster_start = perf_counter()
        for rel_path in atlas["files"]:
            parts = rel_path.replace("src/", "").split("/")
            if "features" in parts:
                idx = parts.index("features")
                if len(parts) > idx + 1:
                    feat = parts[idx + 1]
                    if feat not in atlas["features"]:
                        atlas["features"][feat] = {"files": []}
                    atlas["features"][feat]["files"].append(rel_path)

            cluster = parts[0]
            if cluster and not cluster.endswith((".ts", ".tsx")):
                if cluster not in atlas["clusters"]:
                    atlas["clusters"][cluster] = []
                atlas["clusters"][cluster].append(rel_path)
        cluster_elapsed = perf_counter() - cluster_start

        project_elapsed = perf_counter() - project_start
        # [Phase 10] Local Registry for metric/local use
        api_registry_local = {}
        for r_path, f_d in atlas["files"].items():
            for feat in f_d.get("features", []):
                if feat.startswith("route_path:"):
                    api_registry_local[feat.split(":", 1)[1]] = r_path
        
        # Cross-language linking within the same project
        for r_path, f_d in atlas["files"].items():
            for cand in f_d.get("api_candidates", []):
                if cand in api_registry_local:
                    target = api_registry_local[cand]
                    if target != r_path and target not in atlas["dependencies"][r_path]:
                        atlas["dependencies"][r_path].append(target)

        metric = {
            "project": pkey,
            "files": len(atlas["files"]),
            "pending_ast_files": len(pending_ast_files),
            "lifted_by_mtime": lifted_by_mtime,
            "lifted_by_fingerprint": lifted_by_fingerprint,
            "lifted_by_hash": lifted_by_hash,
            "lifted_by_staging": lifted_by_staging,
            "lifted_sequencer_results": lifted_sequencer_results,
            "staging_files_persisted": staging_files_persisted,
            "staging_files_partitioned": staging_files_partitioned,
            "staging_sequencer_results_partitioned": staging_sequencer_results_partitioned,
            "staging_files_skipped_oversize": staging_files_skipped_oversize,
            "walk_s": round(walk_elapsed, 3),
            "content_s": round(content_elapsed, 3),
            "ast_s": round(ast_elapsed, 3),
            "ast_batch_workers": int(ast_strategy.get("workers_effective", ast_strategy.get("workers", 0)) or 0),
            "ast_batch_chunk_size": int(ast_strategy.get("chunk_size", 0) or 0),
            "ast_batch_jobs": int(ast_strategy.get("chunk_jobs", 0) or 0),
            "ast_batch_adaptive": bool(ast_strategy.get("adaptive_mode", False)),
            "ast_strategy": ast_strategy.get("strategy", "none"),
            "ast_process_starts": int(ast_strategy.get("process_starts", 0) or 0),
            "ast_batch_process_starts": int(ast_strategy.get("batch_process_starts", 0) or 0),
            "ast_fallback_process_starts": int(ast_strategy.get("fallback_process_starts", 0) or 0),
            "ast_batch_failures": int(ast_strategy.get("batch_failures", 0) or 0),
            "ast_fallback_chunks": int(ast_strategy.get("fallback_chunks", 0) or 0),
            "ast_response_identity_failures": int(ast_strategy.get("response_identity_failures", 0) or 0),
            "ast_worker_restarts": int(ast_strategy.get("worker_restarts", 0) or 0),
            "ast_request_replays": int(ast_strategy.get("request_replays", 0) or 0),
            "ast_session_pool_workers": int(ast_strategy.get("session_pool_workers", 0) or 0),
            "ast_node_files_requested": int(ast_strategy.get("node_files_requested", 0) or 0),
            "ast_subprocess_s": round(float(ast_strategy.get("subprocess_duration_seconds", 0.0) or 0.0), 3),
            "ast_node_rss_max_bytes": int(ast_strategy.get("node_reported_rss_max_bytes", 0) or 0),
            "ast_checkpoint_callbacks": int(ast_strategy.get("checkpoint_callbacks", 0) or 0),
            "ast_checkpoint_files": int(ast_strategy.get("checkpoint_files", 0) or 0),
            "ast_checkpoint_s": round(float(ast_strategy.get("checkpoint_seconds", 0.0) or 0.0), 3),
            "sequencer_warnings": ast_strategy.get("warnings", []),
            "enrich_s": round(enrich_elapsed, 3),
            "index_s": round(index_elapsed, 3),
            "cluster_s": round(cluster_elapsed, 3),
            "total_s": round(project_elapsed, 3),
        }
        return index, pkey, atlas, project_changed_files, project_dna_changed_files, metric

    # Build atlas per project
    pre_build_seconds = perf_counter() - atlas_start
    build_phase_start = perf_counter()
    multi_atlas = {}
    changed_files = []
    dna_changed_files = []
    project_metrics = []
    build_jobs = []
    project_items = list(projects.items())
    for index, (pkey, ppath) in enumerate(project_items):
        if effective_stale_projects is not None and pkey not in effective_stale_projects and pkey in prev_atlas:
            logger.info(f"[DNA] [LIFT] Atlas for {pkey} lifted from cache.")
            lifted_atlas = prev_atlas[pkey]
            lifted_atlas.setdefault("project", {}).setdefault("sequencer_evidence", {
                "scope": "cache_lift_without_current_parser_execution",
                "repository_wide_claim": False,
                "claim_boundary": (
                    "The project was lifted from a prior Atlas artifact. This run did not execute "
                    "a parser batch, so current-run parser coverage is unavailable."
                ),
                "coverage": {
                    "strategy": "cache-lift",
                    "details": [],
                    "warnings": [{
                        "strategy": "cache-lift",
                        "warning": "Current-run parser coverage was not observed for this lifted project.",
                    }],
                },
            })
            multi_atlas[pkey] = lifted_atlas
            continue
        if effective_stale_projects is not None and pkey not in effective_stale_projects and dry_run:
            logger.info(f"[DNA] [SKIP] Atlas for {pkey} omitted in dry-run because project is not stale.")
            continue
        surgical_scope = None
        if pkey in surgical_files_by_project and pkey in prev_atlas and pkey not in missing_cached_projects:
            surgical_scope = surgical_files_by_project[pkey]
        build_jobs.append((index, pkey, ppath, surgical_scope))

    if build_jobs:
        estimated_file_count = estimate_atlas_job_file_count(build_jobs)
        worker_count = atlas_project_worker_count(len(build_jobs), estimated_file_count)
        logger.info(f"[PROFILE] Atlas project workers: {worker_count}")

        if worker_count <= 1:
            build_results = [
                build_project_atlas(index, pkey, ppath, worker_count, surgical_scope)
                for index, pkey, ppath, surgical_scope in build_jobs
            ]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [
                    executor.submit(build_project_atlas, index, pkey, ppath, worker_count, surgical_scope)
                    for index, pkey, ppath, surgical_scope in build_jobs
                ]
                build_results = [future.result() for future in futures]

        build_results.sort(key=lambda item: item[0])
        for _, pkey, atlas, project_changed, project_dna_changed, metric in build_results:
            multi_atlas[pkey] = atlas
            changed_files.extend(project_changed)
            dna_changed_files.extend(project_dna_changed)
            project_metrics.append(metric)
            logger.info(
                "[PROFILE] Atlas %s | files=%s ast=%s lift_mtime=%s lift_fingerprint=%s lift_hash=%s ast_workers=%s ast_chunk=%s ast_jobs=%s ast_adaptive=%s stage_partitioned=%s stage_seq_partitioned=%s stage_skipped=%s walk=%.2fs content=%.2fs ast_batch=%.2fs enrich=%.2fs index=%.2fs cluster=%.2fs total=%.2fs"
                % (
                    pkey,
                    metric["files"],
                    metric["pending_ast_files"],
                    metric.get("lifted_by_mtime", 0),
                    metric.get("lifted_by_fingerprint", 0),
                    metric.get("lifted_by_hash", 0),
                    metric.get("ast_batch_workers", 0),
                    metric.get("ast_batch_chunk_size", 0),
                    metric.get("ast_batch_jobs", 0),
                    "yes" if metric.get("ast_batch_adaptive", False) else "no",
                    metric.get("staging_files_partitioned", 0),
                    metric.get("staging_sequencer_results_partitioned", 0),
                    metric.get("staging_files_skipped_oversize", 0),
                    metric["walk_s"],
                    metric["content_s"],
                    metric["ast_s"],
                    metric["enrich_s"],
                    metric["index_s"],
                    metric["cluster_s"],
                    metric["total_s"],
                )
            )

    build_phase_seconds = perf_counter() - build_phase_start

    selected_project_keys = sorted(str(project_key) for project_key in multi_atlas)
    bounded_projection = bool(surgical_files_by_project or runtime_config.PROJECT_FILTER)
    preserved_bounded_projects = preserve_unselected_bounded_projects(
        multi_atlas,
        prev_atlas,
        bounded_projection=bounded_projection,
    )
    if bounded_projection:
        logger.info(
            "[FAST] [BOUNDED] Canonical Atlas project disposition selected=%s replaced=%s preserved=%s removed=0 preserved_projects=%s",
            len(projects),
            len(selected_project_keys),
            len(preserved_bounded_projects),
            preserved_bounded_projects,
        )

    # [Phase 11] Global Semantic Bridge (Cross-Project)
    # This is the "Nanometric Bridge" between disparate projects (e.g. Project A Backend -> Project B Frontend)
    bridge_start = perf_counter()
    apply_global_semantic_bridge(
        multi_atlas,
        mutable_project_keys=set(selected_project_keys) if bounded_projection else None,
    )
    bridge_seconds = perf_counter() - bridge_start
    payload_validation_seconds = 0.0
    atlas_persist_seconds = 0.0
    atlas_commit_seconds = 0.0
    workload_profile_seconds = 0.0
    ram_cache_seconds = 0.0
    atlas_persist_profile = {}
    if not dry_run:
        payload_validation_start = perf_counter()
        ensure_valid_payload("atlas", multi_atlas)
        payload_validation_seconds = perf_counter() - payload_validation_start
        output_path = RAW_DIR / 'atlas.json'
        previous_snapshot_scope = DYNAMIC_CONFIG.get("_source_snapshot_projection_scope")
        previous_state_reuse_hint = DYNAMIC_CONFIG.get("_atlas_state_payload_reuse")
        bounded_snapshot_scope = bounded_atlas_snapshot_scope(
            multi_atlas,
            projects,
            surgical_files_by_project,
            project_filter_active=bool(runtime_config.PROJECT_FILTER),
        )
        if bounded_snapshot_scope:
            DYNAMIC_CONFIG["_source_snapshot_projection_scope"] = bounded_snapshot_scope
        else:
            DYNAMIC_CONFIG.pop("_source_snapshot_projection_scope", None)
        atlas_persist_start = perf_counter()
        state_reuse_hint = {}
        if isinstance(prev_atlas, dict) and prev_atlas and prev_atlas == multi_atlas:
            state_reuse_hint = atlas_state_payload_reuse_hint(
                prev_atlas,
                multi_atlas,
                raw_artifact_content_fingerprint(output_path),
            )
        if state_reuse_hint:
            DYNAMIC_CONFIG["_atlas_state_payload_reuse"] = state_reuse_hint
        else:
            DYNAMIC_CONFIG.pop("_atlas_state_payload_reuse", None)
        try:
            try:
                atlas_persist_profile = save_json_atomic(output_path, multi_atlas) or {}
            except Exception as exc:
                if staging_store is not None and staging_run_id:
                    try:
                        staging_store.finish_atlas_staging_run(
                            staging_run_id,
                            staging_producer_contract,
                            status="FAILED",
                            error_type=type(exc).__name__,
                        )
                    except Exception as receipt_exc:
                        logger.error("[ATLAS_STAGING] Failure receipt could not be persisted: %s", receipt_exc)
                raise
        finally:
            if previous_snapshot_scope is None:
                DYNAMIC_CONFIG.pop("_source_snapshot_projection_scope", None)
            else:
                DYNAMIC_CONFIG["_source_snapshot_projection_scope"] = previous_snapshot_scope
            if previous_state_reuse_hint is None:
                DYNAMIC_CONFIG.pop("_atlas_state_payload_reuse", None)
            else:
                DYNAMIC_CONFIG["_atlas_state_payload_reuse"] = previous_state_reuse_hint
        atlas_persist_seconds = perf_counter() - atlas_persist_start
        generation_mode = atlas_generation_mode(effective_stale_projects, projects.keys(), surgical_files)
        atlas_commit_start = perf_counter()
        changed_file_refs = [
            f"{project_key}::{rel_path}"
            for project_key, paths in sorted(surgical_files_by_project.items())
            for rel_path in sorted(paths)
        ]
        deleted_file_refs = [
            f"{project_key}::{rel_path}"
            for project_key, paths in sorted(surgical_files_by_project.items())
            for rel_path in sorted(paths)
            if not (Path(projects[project_key]) / Path(rel_path)).is_file()
        ]
        atlas_commit = build_atlas_commit(
            multi_atlas,
            generation_mode=generation_mode,
            atlas_sha256=str(atlas_persist_profile.get("state_payload_sha256") or ""),
            parent_snapshot_id=str(previous_atlas_commit.get("snapshot_id") or ""),
            changed_files=changed_file_refs,
            deleted_files=deleted_file_refs,
        )
        ensure_valid_payload("atlas_commit", atlas_commit)
        save_json_atomic(RAW_DIR / "atlas_commit.json", atlas_commit)
        atlas_commit_seconds = perf_counter() - atlas_commit_start
        workload_profile_start = perf_counter()
        workload_profile = build_workload_profile(
            multi_atlas,
            snapshot_id=str(atlas_commit.get("snapshot_id") or ""),
            execution_profile=generation_mode,
        )
        ensure_valid_payload("workload_profile", workload_profile)
        save_json_atomic(RAW_DIR / "workload_profile.json", workload_profile)
        workload_profile_seconds = perf_counter() - workload_profile_start
        if staging_store is not None and staging_run_id:
            staging_store.finish_atlas_staging_run(
                staging_run_id,
                staging_producer_contract,
                status="COMPLETED",
            )
        logger.info(f"[OK] Generated multi-project atlas: {output_path} ({len(multi_atlas)} projects)")
        
        # [Phase 6] RAM Persistence for Genomic Orchestration
        global GLOBAL_ATLAS_CACHE
        ram_cache_start = perf_counter()
        GLOBAL_ATLAS_CACHE.clear()
        GLOBAL_ATLAS_CACHE.update(multi_atlas)
        ram_cache_seconds = perf_counter() - ram_cache_start
    else:
        logger.info(f"[TEST] [DRY-RUN] Atlas data generated but not written to disk.")
    total_elapsed = perf_counter() - atlas_start
    if project_metrics:
        logger.info(
            "[PROFILE] Atlas Node AST lifecycle | process_starts=%s batch_starts=%s fallback_starts=%s "
            "batch_failures=%s fallback_chunks=%s identity_failures=%s worker_restarts=%s "
            "request_replays=%s files_requested=%s "
            "checkpoint_callbacks=%s checkpoint_files=%s subprocess=%.3fs checkpoint=%.3fs node_rss_max_bytes=%s"
            % (
                sum(m.get("ast_process_starts", 0) for m in project_metrics),
                sum(m.get("ast_batch_process_starts", 0) for m in project_metrics),
                sum(m.get("ast_fallback_process_starts", 0) for m in project_metrics),
                sum(m.get("ast_batch_failures", 0) for m in project_metrics),
                sum(m.get("ast_fallback_chunks", 0) for m in project_metrics),
                sum(m.get("ast_response_identity_failures", 0) for m in project_metrics),
                sum(m.get("ast_worker_restarts", 0) for m in project_metrics),
                sum(m.get("ast_request_replays", 0) for m in project_metrics),
                sum(m.get("ast_node_files_requested", 0) for m in project_metrics),
                sum(m.get("ast_checkpoint_callbacks", 0) for m in project_metrics),
                sum(m.get("ast_checkpoint_files", 0) for m in project_metrics),
                sum(m.get("ast_subprocess_s", 0.0) for m in project_metrics),
                sum(m.get("ast_checkpoint_s", 0.0) for m in project_metrics),
                max([m.get("ast_node_rss_max_bytes", 0) for m in project_metrics] or [0]),
            )
        )
        logger.info(
            "[PROFILE] Atlas persistence | mode=%s parts=%s chars=%s bytes=%s stream_chunks=%s serialize=%.3fs encode=%.3fs hash=%.3fs sqlite=%.3fs relational=%.3fs total=%.3fs"
            % (
                str(atlas_persist_profile.get("state_payload_storage_mode") or "not_available"),
                int(atlas_persist_profile.get("state_payload_part_count", 0) or 0),
                int(atlas_persist_profile.get("state_payload_chars", 0) or 0),
                int(atlas_persist_profile.get("state_payload_bytes", 0) or 0),
                int(atlas_persist_profile.get("state_payload_stream_chunks", 0) or 0),
                float(atlas_persist_profile.get("state_payload_serialize_seconds", 0.0) or 0.0),
                float(atlas_persist_profile.get("state_payload_encode_seconds", 0.0) or 0.0),
                float(atlas_persist_profile.get("state_payload_hash_seconds", 0.0) or 0.0),
                float(atlas_persist_profile.get("state_payload_sqlite_seconds", 0.0) or 0.0),
                float(atlas_persist_profile.get("atlas_relational_index_seconds", 0.0) or 0.0),
                float(atlas_persist_profile.get("total_save_raw_seconds", 0.0) or 0.0),
            )
        )
        logger.info(
            "[PROFILE] Atlas materialization | generation=%s canonical_projects=%s canonical_files=%s "
            "relational_mode=%s relational_projects=%s relational_files=%s scoped_projects=%s "
            "scoped_files=%s unselected_projects=%s dependency_sources=%s baseline_status=%s "
            "baseline_reason=%s state_reuse=%s reused_bytes=%s state_write=%.3fs "
            "relational=%.3fs transaction=%.3fs commit=%.3fs"
            % (
                str(atlas_persist_profile.get("atlas_materialization_generation_id") or "not_available"),
                int(atlas_persist_profile.get("atlas_canonical_project_count", 0) or 0),
                int(atlas_persist_profile.get("atlas_canonical_file_count", 0) or 0),
                str(atlas_persist_profile.get("atlas_relational_mode") or "not_available"),
                int(atlas_persist_profile.get("atlas_relational_project_count", 0) or 0),
                int(atlas_persist_profile.get("atlas_relational_file_count", 0) or 0),
                int(atlas_persist_profile.get("atlas_scoped_projects", 0) or 0),
                int(atlas_persist_profile.get("atlas_scoped_files", 0) or 0),
                int(atlas_persist_profile.get("atlas_unselected_canonical_projects", 0) or 0),
                int(atlas_persist_profile.get("atlas_dependency_sources_updated", 0) or 0),
                str(atlas_persist_profile.get("atlas_scoped_baseline_status") or "not_available"),
                str(atlas_persist_profile.get("atlas_scoped_baseline_reason") or "not_available"),
                str(atlas_persist_profile.get("state_payload_reuse_status") or "not_available"),
                int(atlas_persist_profile.get("state_payload_reused_bytes", 0) or 0),
                float(atlas_persist_profile.get("atlas_state_payload_write_seconds", 0.0) or 0.0),
                float(atlas_persist_profile.get("atlas_relational_index_seconds", 0.0) or 0.0),
                float(atlas_persist_profile.get("atlas_primary_transaction_seconds", 0.0) or 0.0),
                float(atlas_persist_profile.get("atlas_transaction_commit_seconds", 0.0) or 0.0),
            )
        )
        logger.info(
            "[PROFILE] Atlas phases | pre_build=%.3fs build=%.3fs bridge=%.3fs validate=%.3fs persist=%.3fs commit=%.3fs workload=%.3fs ram_cache=%.3fs"
            % (
                pre_build_seconds,
                build_phase_seconds,
                bridge_seconds,
                payload_validation_seconds,
                atlas_persist_seconds,
                atlas_commit_seconds,
                workload_profile_seconds,
                ram_cache_seconds,
            )
        )
        logger.info(
            "[PROFILE] Atlas total | projects=%s files=%s total=%.2fs"
            % (
                len(project_metrics),
                sum(m["files"] for m in project_metrics),
                total_elapsed,
            )
        )
    return multi_atlas, changed_files, dna_changed_files


if __name__ == "__main__":
    import sys
    stale_arg = next((arg for arg in sys.argv if arg.startswith("--stale-projects=")), None)
    stale_list = stale_arg.split("=")[1].split(",") if stale_arg else None
    generate_atlas(stale_projects=stale_list)
