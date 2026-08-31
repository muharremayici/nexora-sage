"""
Auto-Merge Plan Generator
Reads the current fractal_map.json contract and generates runnable Powershell / Bash
scripts for low and medium risk physical file migrations into MAIN.
"""
import json
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from typing import Dict, Iterable

from tools.core.config import RAW_DIR, REPORTS_DIR, ROOT, SANCTUARY_DIR, SCRIPTS_DIR, save_json_atomic, save_text_atomic, DOCTRINE
from tools.core.doctrine_contract import require_dead_code_policy, require_doctrine_mapping
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file, load_json_strict
from tools.core.fractal_io import load_fractal_map_data
from tools.core.generated_validation_commands import generated_mutation_post_validation
from tools.core.logger import logger
from tools.core.module_policy import get_semantic_rules
from tools.core.path_engine import to_os_path, to_posix_path
from tools.core.projects_registry import resolve_runtime_projects
from tools.core.source_files import is_analysis_source_file
from tools.core.workspace_mode import get_workspace_mode, is_source_allowed_for_host_merge
from tools.engines.self_healing_generator import CodeHealer
from tools.core.audit_report import get_violations


_IMPORT_PATTERNS = [
    re.compile(r"(?:import|export)\s+[^;]*?\sfrom\s*['\"](@/[^'\"]+)['\"]"),
    re.compile(r"import\s*['\"](@/[^'\"]+)['\"]"),
    re.compile(r"import\(\s*['\"](@/[^'\"]+)['\"]\s*\)"),
]


def _mutation_guard_ps_lines(script_name: str) -> list[str]:
    return [
        f"if ($env:CODEMAPS_ALLOW_MUTATION_SCRIPTS -ne '1') {{",
        f"    Write-Error '{script_name} is blocked by default because it mutates the host source tree. Set CODEMAPS_ALLOW_MUTATION_SCRIPTS=1 only after reviewing the dry-run plan and backups.'",
        "    exit 64",
        "}",
        "",
    ]


def _mutation_guard_bash_lines(script_name: str) -> list[str]:
    return [
        'if [ "${CODEMAPS_ALLOW_MUTATION_SCRIPTS:-}" != "1" ]; then',
        f"  echo '{script_name} is blocked by default because it mutates the host source tree. Set CODEMAPS_ALLOW_MUTATION_SCRIPTS=1 only after reviewing the dry-run plan and backups.' >&2",
        "  exit 64",
        "fi",
        "",
    ]


def _manifest_guard_ps_lines(manifest_name: str) -> list[str]:
    return [
        f"$manifestPath = Join-Path $PSScriptRoot '{manifest_name}'",
        "if (-not (Test-Path -LiteralPath $manifestPath)) {",
        f"    Write-Error 'Required dry-run manifest is missing: {manifest_name}'",
        "    exit 65",
        "}",
        "$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json",
        "$currentScript = Split-Path -Leaf $PSCommandPath",
        "$scriptEntry = $manifest.scripts | Where-Object { $_.name -eq $currentScript } | Select-Object -First 1",
        "if (-not $scriptEntry -or -not $scriptEntry.sha256) {",
        "    Write-Error \"Manifest does not include checksum for $currentScript\"",
        "    exit 66",
        "}",
        "$currentHash = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()",
        "if ($currentHash -ne $scriptEntry.sha256.ToLowerInvariant()) {",
        "    Write-Error \"Script checksum does not match manifest for $currentScript\"",
        "    exit 67",
        "}",
        "",
    ]


def _manifest_guard_bash_lines(manifest_name: str) -> list[str]:
    return [
        'script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"',
        f"manifest_path=\"$script_dir/{manifest_name}\"",
        'if [ ! -f "$manifest_path" ]; then',
        f"  echo 'Required dry-run manifest is missing: {manifest_name}' >&2",
        "  exit 65",
        "fi",
        'current_script="$(basename "$0")"',
        'expected_hash="$(python -c "import json,sys; data=json.load(open(sys.argv[1], encoding=\'utf-8\')); print(next((item.get(\'sha256\', \'\') for item in data.get(\'scripts\', []) if item.get(\'name\') == sys.argv[2]), \'\'))" "$manifest_path" "$current_script")"',
        'if [ -z "$expected_hash" ]; then',
        '  echo "Manifest does not include checksum for $current_script" >&2',
        "  exit 66",
        "fi",
        "if ! command -v sha256sum >/dev/null 2>&1; then",
        "  echo 'sha256sum is required for script checksum verification.' >&2",
        "  exit 66",
        "fi",
        'current_hash="$(sha256sum "$0" | awk \'{print tolower($1)}\')"',
        'expected_hash="$(echo "$expected_hash" | tr "[:upper:]" "[:lower:]")"',
        'if [ "$current_hash" != "$expected_hash" ]; then',
        '  echo "Script checksum does not match manifest for $current_script" >&2',
        "  exit 67",
        "fi",
        "",
    ]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_path(path: str) -> str:
    return to_os_path(path)


def _safe_relative_path(path: str) -> str | None:
    raw = str(path or "").strip()
    if not raw or raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        return None
    normalized = to_posix_path(raw).strip()
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return None
    parts = [part for part in normalized.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        return None
    return to_os_path("/".join(parts))


def _safe_join(root: Path, relative_path: str) -> Path | None:
    safe_relative = _safe_relative_path(relative_path)
    if not safe_relative:
        return None
    resolved_root = Path(root).resolve()
    candidate = (resolved_root / safe_relative).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        return None
    return candidate


def _path_stem(path: str) -> str:
    normalized = to_posix_path(path).split("/")[-1]
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[0]
    return normalized.lower()


def _path_ext(path: str) -> str:
    normalized = to_posix_path(path).split("/")[-1]
    return f".{normalized.rsplit('.', 1)[1].lower()}" if "." in normalized else ""


def _norm_parts(path: str):
    return [p for p in to_posix_path(path).lower().split("/") if p]


def _has_any_part(path: str, values) -> bool:
    parts = set(_norm_parts(path))
    return any(v in parts for v in values)


def _is_component_target(target_path: str) -> bool:
    parts = _norm_parts(target_path)
    return (
        "features" in parts or
        "widgets" in parts or
        "pages" in parts or
        _path_ext(target_path) == ".tsx"
    )


def _is_semantically_safe_copy(candidate: dict) -> bool:
    source_path = str(candidate.get("source_path") or "")
    target_path = str(candidate.get("target_path_suggestion") or "")
    source_ext = _path_ext(source_path)
    target_ext = _path_ext(target_path)
    source_parts = _norm_parts(source_path)
    target_parts = _norm_parts(target_path)

    if not source_path or not target_path:
        return False

    if not is_analysis_source_file(source_path) or not is_analysis_source_file(target_path):
        return False

    safety_rules = require_doctrine_mapping("merge_safety_rules")
    banned = safety_rules.get("banned_source_fragments")
    if _has_any_part(source_path, banned) or any(b in source_path.lower() for b in banned):
        return False

    for rule in safety_rules.get("incompatible_mappings"):
        conditions_met = True
        if "source_has" in rule and not _has_any_part(source_path, rule["source_has"]): conditions_met = False
        if "target_is_component" in rule and not _is_component_target(target_path): conditions_met = False
        if "source_ext" in rule and source_ext != rule["source_ext"]: conditions_met = False
        if "target_ext" in rule and target_ext != rule["target_ext"]: conditions_met = False
        if conditions_met and rule.get("action") == "BLOCK":
            return False

    requirements = safety_rules.get("interface_requirements")
    for layer, req in requirements.items():
        if layer in target_parts:
            must_have = req.get("source_must_have", [])
            has_part = _has_any_part(source_path, must_have)
            has_ext = source_ext == req.get("or_source_ext", "")
            has_token = any(t in source_path.lower() for t in req.get("or_source_contains", [])) or (layer == "hooks" and _path_stem(source_path).startswith("use"))
            
            if not (has_part or has_ext or has_token):
                return False

    return True


def _verify_source_semantics(source_abs: str, candidate: dict) -> tuple[bool, str]:
    target_path = str(candidate.get("target_path_suggestion") or "")
    target_parts = _norm_parts(target_path)
    
    if not os.path.exists(source_abs):
        return False, "Source physical file does not exist"

    try:
        content = open(source_abs, "r", encoding="utf-8").read()
    except Exception:
        try:
            content = open(source_abs, "r", encoding="latin-1", errors="ignore").read()
        except Exception:
            return False, "Failed to read physical source file content"

    rules = get_semantic_rules()
    leaks = set(candidate.get("leaks") or [])
    
    # Rule 1: Strict Aliasing Enforcement (AST-Verified)
    if rules.get("strict_aliasing", True):
        if "RELATIVE_PARENT_ESCAPE" in leaks:
            return False, "Violates Strict Aliasing: File contains relative parent escapes (../)"

    # Rule 2: Domain Purity (AST-Verified)
    if "domain" in target_parts:
        for forbidden in rules.get("domain_restricted_imports", []):
            if forbidden in leaks:
                return False, f"Violates Domain Purity: Contains forbidden reference to {forbidden}"

    # Rule 3: Entities Sanctity (AST-Verified)
    if "entities" in target_parts:
        for forbidden in rules.get("entities_restricted_imports", []):
            if forbidden in leaks:
                return False, f"Violates Entities Sanctity: Contains forbidden reference to {forbidden}"
        if rules.get("entities_forbids_async_side_effects", True):
            if "SIDE_EFFECT_FETCH" in leaks or "SIDE_EFFECT_AXIOS" in leaks:
                return False, "Violates Entities Sanctity: Contains raw data-fetching side effects"

    # Rule 4: API Restraints (AST-Verified)
    if "api" in target_parts:
        for forbidden in rules.get("api_restricted_imports", []):
            if forbidden in leaks:
                return False, f"Violates API Restraints: API surface must not reference {forbidden}"

    # Rule 5: UI Layer Separation
    if "features" in target_parts or "pages" in target_parts or "widgets" in target_parts:
        if target_path.endswith("api/index.ts") or target_path.endswith("api/ports.ts"):
            return False, "Violates UI Separation: Cannot place APIs directly within UI folders"

    return True, "Passed Semantic Gate"


def _load_genome_index():
    genome = load_genome_data()
    index = {}
    if not isinstance(genome, dict):
        return index
    for occs in genome.values():
        for occ in occs:
            key = (
                occ.get("project"),
                to_posix_path(occ.get("file", "")),
                occ.get("name"),
            )
            if key not in index:
                index[key] = occ
    return index


def _verify_ast_boundary_semantics(candidate: dict, genome_index: dict) -> tuple[bool, str]:
    target_path = str(candidate.get("target_path_suggestion") or "")
    target_parts = _norm_parts(target_path)
    key = (
        candidate.get("source_project"),
        to_posix_path(candidate.get("source_path", "")),
        candidate.get("name"),
    )
    occ = genome_index.get(key)
    if not occ:
        return True, "No genome occurrence found; AST boundary gate skipped"

    ui_dependencies = set(occ.get("ui_dependencies") or [])
    architectural_markers = set(occ.get("architectural_markers") or [])
    dynamic_imports = set(occ.get("dynamic_imports") or [])
    member_ui_dependencies = set(occ.get("member_ui_dependencies") or [])
    member_dynamic_imports = set(occ.get("member_dynamic_imports") or [])
    member_architectural_markers = set(occ.get("member_architectural_markers") or [])
    member_side_effect_markers = set(occ.get("member_side_effect_markers") or [])
    member_side_effect_imports = set(occ.get("member_side_effect_imports") or [])
    member_side_effect_calls = set(occ.get("member_side_effect_calls") or [])

    all_ui_dependencies = ui_dependencies | member_ui_dependencies
    all_dynamic_imports = dynamic_imports | member_dynamic_imports
    all_architectural_markers = architectural_markers | member_architectural_markers
    side_effect_source = sorted(member_side_effect_imports)[0] if member_side_effect_imports else ""

    safety = require_doctrine_mapping("merge_safety_rules")
    gates = safety.get("ast_boundary_gates")
    
    # Rule 1: Domain Purity
    if "domain" in target_parts:
        gate = gates.get("domain_purity")
        forbidden_markers = set(gate.get("forbidden_markers"))
        forbidden_effects = set(gate.get("forbidden_side_effects"))
        
        if (all_ui_dependencies or forbidden_markers & all_architectural_markers):
            return False, "Violates Domain Purity: AST signals show UI/provider/controller coupling"
        if forbidden_effects & member_side_effect_markers:
            return False, "Violates Domain Purity: member-level side effects show forbidden runtime coupling"

    # Rule 2: Entities Sanctity
    if "entities" in target_parts:
        gate = gates.get("entities_sanctity")
        forbidden_markers = set(gate.get("forbidden_markers"))
        forbidden_effects = set(gate.get("forbidden_side_effects"))
        
        if (all_ui_dependencies or all_dynamic_imports or forbidden_markers & all_architectural_markers):
            return False, "Violates Entities Sanctity: AST signals show UI or dynamic-import coupling"
        if forbidden_effects & member_side_effect_markers:
            return False, "Violates Entities Sanctity: forbidden member-level side-effect signals detected"

    # Rule 3: API Restraints
    if "api" in target_parts:
        gate = gates.get("api_restraints")
        forbidden_markers = set(gate.get("forbidden_markers"))
        forbidden_effects = set(gate.get("forbidden_side_effects"))
        
        if all_ui_dependencies or (forbidden_markers & all_architectural_markers):
            return False, "Violates API Restraints: AST signals show UI/context coupling on API target"
        if forbidden_effects & member_side_effect_markers:
            return False, "Violates Boundary Restraints: member-level side effects show storage or DOM coupling"

    return True, "Passed AST Boundary Gate"


def _build_candidate_index(merge_candidates):
    index = {}
    for item in merge_candidates:
        key = (
            item.get("source_project"),
            item.get("name"),
            item.get("target_path_suggestion"),
        )
        index[key] = item
    return index


def _collect_safe_migrations(fractal, projects):
    merge_candidates = fractal.get("merge_candidates", [])
    merge_waves = fractal.get("merge_waves", {})
    candidate_index = _build_candidate_index(merge_candidates)
    genome_index = _load_genome_index()

    safe_items = []
    metrics = {
        "seen_wave_items": 0,
        "matched_candidates": 0,
        "auto_merge_ready": 0,
        "rejected_review": 0,
        "rejected_risk": 0,
        "rejected_confidence": 0,
        "rejected_path_shape": 0,
        "rejected_semantic_copy": 0,
        "rejected_missing_source_root": 0,
        "rejected_semantic_gate": 0,
        "rejected_ast_boundary_gate": 0,
        "rejected_source_role_policy": 0,
        "planned": 0,
    }
    by_source = defaultdict(
        lambda: {
            "matched_candidates": 0,
            "auto_merge_ready": 0,
            "planned": 0,
            "rejected_review": 0,
            "rejected_risk": 0,
            "rejected_confidence": 0,
            "rejected_semantic_gate": 0,
            "rejected_ast_boundary_gate": 0,
            "rejected_source_role_policy": 0,
        }
    )
    workspace_mode = get_workspace_mode()
    host_projects = workspace_mode.get("host_projects", []) or ["MAIN"]
    target_host = str(host_projects[0] if host_projects else "MAIN")
    for wave_name in ("wave_1_low", "wave_2_medium"):
        for wave_item in merge_waves.get(wave_name, []):
            metrics["seen_wave_items"] += 1
            key = (
                wave_item.get("source_project"),
                wave_item.get("name"),
                wave_item.get("target"),
            )
            candidate = candidate_index.get(key)
            if not candidate:
                continue
            source_project = candidate.get("source_project") or "UNKNOWN"
            metrics["matched_candidates"] += 1
            by_source[source_project]["matched_candidates"] += 1
            source_allowed, source_reason = is_source_allowed_for_host_merge(source_project=source_project, target_project=target_host)
            if not source_allowed:
                logger.info(f"[SKIP] Source role policy blocked {candidate.get('name')} from {source_project}: {source_reason}")
                metrics["rejected_source_role_policy"] += 1
                by_source[source_project]["rejected_source_role_policy"] += 1
                continue
            if candidate.get("merge_readiness") != "auto_merge":
                metrics["rejected_review"] += 1
                by_source[source_project]["rejected_review"] += 1
                continue
            metrics["auto_merge_ready"] += 1
            by_source[source_project]["auto_merge_ready"] += 1
            review_bucket = str(candidate.get("review_bucket") or "").strip().lower()
            review_reasons = candidate.get("review_reasons", [])
            risk = str(candidate.get("risk") or "").upper()
            confidence = float(candidate.get("confidence") or 0.0)
            if review_bucket and review_bucket != "auto_merge_ready":
                metrics["rejected_review"] += 1
                by_source[source_project]["rejected_review"] += 1
                continue
            if isinstance(review_reasons, list) and review_reasons:
                metrics["rejected_review"] += 1
                by_source[source_project]["rejected_review"] += 1
                continue
            if risk not in {"LOW", "MEDIUM"}:
                metrics["rejected_risk"] += 1
                by_source[source_project]["rejected_risk"] += 1
                continue
            heuristics = require_dead_code_policy("assembly_governance").get("decision_heuristics")
            if confidence < heuristics.get("candidate_confidence_threshold", 0.7):
                metrics["rejected_confidence"] += 1
                by_source[source_project]["rejected_confidence"] += 1
                continue
            source_path = candidate.get("source_path")
            target_path = candidate.get("target_path_suggestion")
            if not source_path or not target_path or not source_project:
                metrics["rejected_path_shape"] += 1
                continue
            safe_source_path = _safe_relative_path(source_path)
            safe_target_path = _safe_relative_path(target_path)
            if not safe_source_path or not safe_target_path:
                metrics["rejected_path_shape"] += 1
                continue
            if _path_stem(source_path) != _path_stem(target_path):
                metrics["rejected_path_shape"] += 1
                continue
            if not _is_semantically_safe_copy(candidate):
                metrics["rejected_semantic_copy"] += 1
                continue

            source_root = projects.get(source_project)
            if not source_root:
                metrics["rejected_missing_source_root"] += 1
                continue
            src_abs_path = _safe_join(Path(str(source_root)), source_path)
            dst_abs_path = _safe_join(Path(str(ROOT)), target_path)
            if src_abs_path is None or dst_abs_path is None:
                metrics["rejected_path_shape"] += 1
                continue
            src_abs = str(src_abs_path)
            
            # Phase 2 Semantic Validation
            is_valid, reason = _verify_source_semantics(src_abs, candidate)
            if not is_valid:
                logger.warning(f"[BLOCKED] {candidate.get('name')}: {reason} ({source_path} -> {target_path})")
                metrics["rejected_semantic_gate"] += 1
                by_source[source_project]["rejected_semantic_gate"] += 1
                continue

            is_valid, reason = _verify_ast_boundary_semantics(candidate, genome_index)
            if not is_valid:
                logger.warning(f"[BLOCKED] {candidate.get('name')}: {reason} ({source_path} -> {target_path})")
                metrics["rejected_ast_boundary_gate"] += 1
                by_source[source_project]["rejected_ast_boundary_gate"] += 1
                continue

            safe_items.append({
                "name": candidate.get("name"),
                "wave": wave_name,
                "source_project": source_project,
                "source_path": safe_source_path,
                "target_path": safe_target_path,
            })
            metrics["planned"] += 1
            by_source[source_project]["planned"] += 1

    dedup = {}
    for item in safe_items:
        dedup[(item["source_project"], item["source_path"], item["target_path"])] = item
    deduped = list(dedup.values())
    metrics["planned"] = len(deduped)
    return deduped, metrics, dict(by_source)


def _confidence_from_metrics(metrics):
    matched = int(metrics.get("matched_candidates", 0) or 0)
    auto_ready = int(metrics.get("auto_merge_ready", 0) or 0)
    planned = int(metrics.get("planned", 0) or 0)
    rejected_review = int(metrics.get("rejected_review", 0) or 0)
    rejected_boundary = int(metrics.get("rejected_ast_boundary_gate", 0) or 0)
    if matched <= 0:
        return {
            "score": 1.0,
            "tier": "not_applicable",
            "signals": {
                "matched_candidates": 0,
                "auto_ready_ratio": 1.0,
                "plan_yield_ratio": 1.0,
                "review_rejection_ratio": 0.0,
                "boundary_rejection_ratio": 0.0,
            },
        }
    auto_ratio = auto_ready / matched
    plan_yield = planned / max(1, auto_ready)
    review_reject_ratio = rejected_review / matched
    boundary_reject_ratio = rejected_boundary / max(1, auto_ready)
    score = (0.35 * auto_ratio) + (0.35 * plan_yield) + (0.15 * (1.0 - review_reject_ratio)) + (0.15 * (1.0 - boundary_reject_ratio))
    score = max(0.0, min(1.0, round(score, 3)))
    if score >= 0.80:
        tier = "high"
    elif score >= 0.60:
        tier = "medium"
    else:
        tier = "low"
    return {
        "score": score,
        "tier": tier,
        "signals": {
            "matched_candidates": matched,
            "auto_ready_ratio": round(auto_ratio, 3),
            "plan_yield_ratio": round(plan_yield, 3),
            "review_rejection_ratio": round(review_reject_ratio, 3),
            "boundary_rejection_ratio": round(boundary_reject_ratio, 3),
        },
    }


def _load_blast_radius_map() -> Dict[str, float]:
    """Loads blast radius impact scores from raw analysis."""
    impact_map = {}
    from pathlib import Path
    # Ensure RAW_DIR is treated as Path regardless of its source type
    blast_radius_path = Path(str(RAW_DIR)) / "blast_radius.json"

    try:
        from tools.core.json_io import load_json_file
        data = load_json_file(blast_radius_path, {})
        items = data.get("blast_radius", [])
        for item in items:
            key = item.get("file") # PROJECT::path/to/file.ts
            score = item.get("total_impact_score", 0.0)
            if key:
                impact_map[key] = float(score)
    except Exception as e:
        logger.error(f"Failed to parse blast radius map: {e}")
        
    return impact_map


def _iter_alias_imports(content: str) -> Iterable[str]:
    found = []
    for pattern in _IMPORT_PATTERNS:
        found.extend(pattern.findall(content))
    return list(dict.fromkeys(found))


def _resolve_alias_in_project(project_root: Path, alias: str) -> Path | None:
    if not alias.startswith("@/"):
        return None
    raw = alias.replace("@/", "", 1).lstrip("/")
    rel_candidates = [raw]
    if raw.startswith("src/"):
        rel_candidates.append(raw[4:])
    else:
        rel_candidates.append(f"src/{raw}")
    rel_candidates = list(dict.fromkeys([c for c in rel_candidates if c]))

    roots = [project_root]
    project_src = project_root / "src"
    if project_src.exists():
        roots.append(project_src)

    extensions = [".ts", ".tsx", ".js", ".jsx", ".d.ts"]
    for root in roots:
        for rel in rel_candidates:
            base = root / rel
            if base.exists() and base.is_file():
                return base.resolve()
            for ext in extensions:
                with_ext = Path(f"{base}{ext}")
                if with_ext.exists() and with_ext.is_file():
                    return with_ext.resolve()
            for idx in ("index.ts", "index.tsx", "index.js", "index.jsx", "index.d.ts"):
                idx_path = base / idx
                if idx_path.exists() and idx_path.is_file():
                    return idx_path.resolve()
    return None


def _hydrate_sanctuary_dependency_closure(migrations, projects, sanitizer_by_project, violations_by_project_file):
    """
    Build sanctuary-local dependency closure for staged files.
    This eliminates Oracle false positives caused by missing alias dependencies.
    """
    copied = 0
    discovered = 0
    unresolved_aliases = set()

    queue_by_project = defaultdict(list)
    visited_by_project = defaultdict(set)
    staged_by_project = defaultdict(set)

    for item in migrations:
        project_name = item.get("source_project")
        rel_source = to_posix_path(item.get("source_path", "")).lstrip("/")
        if not project_name or not rel_source:
            continue
        staged_by_project[project_name].add(rel_source)
        queue_by_project[project_name].append(rel_source)

    for project_name, queue in queue_by_project.items():
        project_root = Path(str(projects.get(project_name, ""))).resolve()
        if not str(project_root):
            continue

        healer = sanitizer_by_project.get(project_name)
        if healer is None:
            healer = CodeHealer(project_name=project_name, workspace_root=str(ROOT))
            sanitizer_by_project[project_name] = healer

        while queue:
            rel = to_posix_path(queue.pop(0)).lstrip("/")
            if rel in visited_by_project[project_name]:
                continue
            visited_by_project[project_name].add(rel)

            source_abs = (project_root / rel).resolve()
            if not source_abs.exists() or not source_abs.is_file():
                continue

            try:
                content = source_abs.read_text(encoding="utf-8")
            except Exception:
                try:
                    content = source_abs.read_text(encoding="latin-1", errors="ignore")
                except Exception as exc:
                    logger.debug("Skipping dependency closure source read for %s: %s", rel, exc)
                    continue

            # Always keep sanctuary hydrated with purified content.
            rel_norm = to_posix_path(rel)
            file_violations = violations_by_project_file.get((project_name, rel_norm), [])
            target_hint = rel_norm if rel_norm.startswith("src/") else f"src/{rel_norm}"
            purified = healer.heal_content(content, file_violations, target_hint, original_path=rel_norm)
            sanctuary_abs = SANCTUARY_DIR / project_name / rel_norm
            sanctuary_abs.parent.mkdir(parents=True, exist_ok=True)
            save_text_atomic(sanctuary_abs, purified)
            copied += 1

            aliases = _iter_alias_imports(content)
            for alias in aliases:
                resolved_abs = _resolve_alias_in_project(project_root, alias)
                if resolved_abs is None:
                    unresolved_aliases.add((project_name, alias))
                    continue
                rel_dep = to_posix_path(os.path.relpath(str(resolved_abs), str(project_root))).lstrip("/")
                if rel_dep not in visited_by_project[project_name]:
                    queue.append(rel_dep)
                    if rel_dep not in staged_by_project[project_name]:
                        discovered += 1
                        staged_by_project[project_name].add(rel_dep)

    return {
        "copied_to_sanctuary": copied,
        "closure_discovered": discovered,
        "unresolved_aliases": len(unresolved_aliases),
    }


def run_merge_script_generator():
    logger.info("Generating Auto-Merge Execution Script...")
    mutation_post_validation = generated_mutation_post_validation()

    fractal = load_fractal_map_data()
    if not isinstance(fractal, dict) or not fractal:
        logger.error("fractal_map.json not found! Pipeline needs to run Fractal Mapping first.")
        return False
    projects = resolve_runtime_projects(ROOT)
    workspace_mode = get_workspace_mode()
    
    # PHASE 4: Risk Intelligence 
    impact_map = _load_blast_radius_map()
    risk_policy = require_doctrine_mapping("dashboard_risk_policy")
    risk_threshold = risk_policy.get("quarantine_threshold")

    host_projects = list((workspace_mode or {}).get("host_projects", []) or [])
    host_project_key = next((key for key in host_projects if key in projects), None)
    if host_project_key is None and "MAIN" in projects:
        host_project_key = "MAIN"
    if host_project_key is None and projects:
        host_project_key = sorted(projects.keys())[0]

    if host_project_key is None:
        logger.error("No host project found in registry. Cannot generate merge script.")
        return False

    migrations, collection_metrics, by_source_metrics = _collect_safe_migrations(fractal, projects)

    # [PHASE 6.0] Symbol Harvesting Pass
    # We leverage the Oracle's Broken Import reports to pull in missing dependencies.
    from tools.engines.convergence_engine import ConvergenceEngine
    convergence = ConvergenceEngine(str(ROOT))
    
    harvested_total = []
    for project_name in list(projects.keys()):
        if project_name == host_project_key:
            continue
        
        # Convergence now returns List[str] or List[Tuple[str, str]] depending on discovery depth
        recovered_data = convergence.recover_dangling_imports(project_name)
        for entry in recovered_data:
            if isinstance(entry, tuple):
                rel, source_project = entry
            else:
                rel, source_project = entry, project_name
                
            # Check if already in migrations
            if any(m["source_path"] == rel and m["source_project"] == source_project for m in migrations):
                continue
            
            # Suggest a target path based on the alias structure (Mirroring MAIN doctrine)
            # FORCE salvaged files into a 'src/' folder in Sanctuary to match Oracle rules
            sanctuary_target = rel
            if not rel.startswith("src/"):
                sanctuary_target = f"src/{rel}"
                
            harvested_total.append({
                "source_project": source_project,
                "source_path": rel,
                "target_path": sanctuary_target, 
                "name": f"[HARVESTED] {os.path.basename(rel)}",
                "confidence": 0.85
            })
    
    if harvested_total:
        logger.info(f"[CONVERGENCE] Symbol Harvesting recovered {len(harvested_total)} missing dependencies.")
        migrations.extend(harvested_total)

    ps_lines = [
        "<#",
        " .SYNOPSIS",
        "  Surgical Auto-Merge Script for Nexora SAGE",
        " .DESCRIPTION",
        "  Generated from the current fractal_map.json contract.",
        "  Includes only low and medium risk merge wave candidates.",
        "#>",
        "",
        "$ErrorActionPreference = 'Stop'",
        "",
    ]
    ps_lines.extend(_mutation_guard_ps_lines("auto_merge.ps1"))
    ps_lines.extend(_manifest_guard_ps_lines("auto_merge_manifest.json"))

    bash_lines = [
        "#!/bin/bash",
        "# Surgical Auto-Merge Script for Nexora SAGE",
        "# Includes only low and medium risk merge wave candidates.",
        "set -e",
        "",
    ]
    bash_lines.extend(_mutation_guard_bash_lines("auto_merge.sh"))
    bash_lines.extend(_manifest_guard_bash_lines("auto_merge_manifest.json"))

    merge_count = 0
    skipped_count = 0
    manifest_operations = []
    file_violations_index = defaultdict(list)
    all_violations = get_violations()
    for violation in all_violations:
        v_project = violation.get("project")
        v_file = to_posix_path(violation.get("file", "")).lstrip("/")
        if v_project and v_file:
            file_violations_index[(v_project, v_file)].append(violation)
    healer_cache = {}

    for item in migrations:
        source_project = item["source_project"]
        source_root = projects.get(source_project)
        if not source_root:
            skipped_count += 1
            continue

        rel_source = _safe_relative_path(item["source_path"])
        rel_target = _safe_relative_path(item["target_path"])
        if not rel_source or not rel_target:
            skipped_count += 1
            continue

        src_abs_path = _safe_join(Path(str(source_root)), rel_source)
        dst_abs_path = _safe_join(Path(str(ROOT)), rel_target)
        if src_abs_path is None or dst_abs_path is None:
            skipped_count += 1
            continue
        src_abs = str(src_abs_path)
        
        # --- HEAL-ON-MERGE TRANSFORMATION ---
        healed_dir = SANCTUARY_DIR / source_project
        healed_path = healed_dir / rel_source
        
        # Match by relative path (since it's what audit.py records)
        src_posix = to_posix_path(rel_source)
        file_violations = file_violations_index.get((source_project, src_posix), [])

        healer = healer_cache.get(source_project)
        if healer is None:
            healer = CodeHealer(project_name=source_project, workspace_root=str(ROOT))
            healer_cache[source_project] = healer
        needs_healing = any(v.get("rule") in healer.healing_policy for v in file_violations)
        src_for_script = src_abs

        # [PHASE 5.0] Always invoke the Healer for Sanctuary purity (Sovereign Mapping)
        try:
            content = open(src_abs, "r", encoding="utf-8").read()
            purified_content = healer.heal_content(content, file_violations, rel_target, original_path=rel_source)
            
            healed_path.parent.mkdir(parents=True, exist_ok=True)
            save_text_atomic(healed_path, purified_content)
            src_for_script = str(healed_path)
            logger.info(f"[PURIFIED] {item['name']} achieved architectural alignment in Sanctuary.")
            
            # PHASE 3: Physical Port Generation
            pending_ports = healer.get_pending_ports()
            for port in pending_ports:
                    # Determine sanctuary path for the new port
                    # Typically domain/ports sibling to logic
                    port_rel = os.path.dirname(rel_source).replace("logic", "ports") 
                    if "domain" not in port_rel:
                        port_rel = os.path.join(os.path.dirname(rel_source), "ports")
                    
                    port_file_rel = os.path.join(port_rel, f"{port['name']}.ts")
                    port_abs_path = SANCTUARY_DIR / source_project / port_file_rel
                    port_abs_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Target structure for the migration
                    target_port_rel = os.path.join(os.path.dirname(rel_target), "ports", f"{port['name']}.ts")
                    target_port_path = _safe_join(Path(str(ROOT)), target_port_rel)
                    if target_port_path is None:
                        continue
                    target_port_abs = str(target_port_path)
                    
                    port_content = f"/**\n * ARCHITECT_PORT: {port['name']}\n * Generated from infra: {port['infra_source']}\n */\n"
                    port_content += f"export interface {port['name']} {{\n"
                    for m in port['members']:
                        port_content += f"    {m}: any; // TODO: Refine Port Member Signature\n"
                    port_content += "}\n"
                    
                    save_text_atomic(port_abs_path, port_content)
                    backup_port_path = SCRIPTS_DIR / "backups" / "auto_merge" / target_port_rel
                    rollback_port = {
                        "if_destination_existed": "restore_backup",
                        "if_destination_missing_before_merge": "delete_destination",
                        "backup_path": str(backup_port_path),
                    }
                    
                    # Add to script lines
                    ps_lines.append(f"if (!(Test-Path -Path '{os.path.dirname(target_port_abs)}')) {{ New-Item -ItemType Directory -Path '{os.path.dirname(target_port_abs)}' -Force }}")
                    ps_lines.append(f"if (Test-Path -LiteralPath '{target_port_abs}') {{ New-Item -ItemType Directory -Force -Path '{str(backup_port_path.parent)}' | Out-Null; Copy-Item -LiteralPath '{target_port_abs}' -Destination '{str(backup_port_path)}' -Force }}")
                    ps_lines.append(f"Copy-Item -Path '{str(port_abs_path)}' -Destination '{target_port_abs}' -Force")
                    bash_lines.append(f"mkdir -p '{os.path.dirname(target_port_abs)}'")
                    bash_lines.append(f"if [ -f '{target_port_abs}' ]; then mkdir -p '{str(backup_port_path.parent).replace(chr(92), '/')}'; cp -f '{target_port_abs}' '{str(backup_port_path).replace(chr(92), '/')}'; fi")
                    bash_lines.append(f"cp '{str(port_abs_path)}' '{target_port_abs}'")
                    manifest_operations.append(
                        {
                            "operation": "copy",
                            "kind": "generated_port",
                            "source_project": source_project,
                            "candidate": item.get("name"),
                            "source": str(port_abs_path),
                            "destination": target_port_abs,
                            "backup_path": str(backup_port_path),
                            "rollback": rollback_port,
                            "post_validation": mutation_post_validation,
                            "risk": "generated_contract",
                            "evidence": {
                                "reason": "pending_port_from_healer",
                                "port_name": port.get("name"),
                                "infra_source": port.get("infra_source"),
                            },
                        }
                    )
                    logger.info(f"[EMITTED] Port {port['name']} for {item['name']}")
        except Exception as e:
            logger.error(f"[ERROR] Failed to heal {rel_source}: {e}")
        
        dst_abs = str(dst_abs_path)

        if not os.path.exists(src_abs) and not needs_healing:
            skipped_count += 1
            continue

        src_ps = src_for_script.replace("/", "\\")
        dst_ps = dst_abs.replace("/", "\\")
        dst_dir_ps = os.path.dirname(dst_ps)

        # PHASE 4: Risk Evaluation
        impact_key = f"{source_project}::{to_posix_path(rel_source)}"
        impact_score = impact_map.get(impact_key, 0.0)
        is_quarantine = impact_score >= risk_threshold
        wave_label = "QUARANTINE" if is_quarantine else item['wave']

        ps_lines.append(
            f"Write-Host 'Merging {item['name']} from {source_project} ({wave_label}) - Impact: {impact_score}...'"
        )
        
        if is_quarantine:
            # Inject interactive gate
            ps_lines.append(f"$answer = Read-Host '   [QUARANTINE] CRITICAL ARCHITECTURAL NODE DETECTED (Impact: {impact_score}). Proceed with merge? [y/n]'")
            ps_lines.append("if ($answer -ne 'y') { Write-Host '   [SKIPPED] User aborted quarantine merge.'; continue }")
            logger.info(f"[RISK] High impact node quarantined: {impact_key} (Score: {impact_score})")

        ps_lines.append(
            f"if (-not (Test-Path -Path '{dst_dir_ps}')) {{ New-Item -ItemType Directory -Force -Path '{dst_dir_ps}' | Out-Null }}"
        )
        backup_path = SCRIPTS_DIR / "backups" / "auto_merge" / rel_target
        backup_ps = str(backup_path).replace("/", "\\")
        backup_dir_ps = os.path.dirname(backup_ps)
        ps_lines.append(f"if (Test-Path -LiteralPath '{dst_ps}') {{ New-Item -ItemType Directory -Force -Path '{backup_dir_ps}' | Out-Null; Copy-Item -LiteralPath '{dst_ps}' -Destination '{backup_ps}' -Force }}")
        ps_lines.append(f"Copy-Item -Path '{src_ps}' -Destination '{dst_ps}' -Force")
        ps_lines.append("")

        src_bash = src_for_script.replace("\\", "/")
        dst_bash = dst_abs.replace("\\", "/")
        dst_dir_bash = os.path.dirname(dst_bash)
        backup_bash = str(backup_path).replace("\\", "/")
        backup_dir_bash = os.path.dirname(backup_bash)

        bash_lines.append(
            f"echo 'Merging {item['name']} from {source_project} ({wave_label}) - Impact: {impact_score}...'"
        )
        
        if is_quarantine:
            # Inject interactive gate (Bash version using 'read')
            bash_lines.append(f"echo -e \"\\033[33m   [QUARANTINE] CRITICAL ARCHITECTURAL NODE DETECTED (Impact: {impact_score})\\033[0m\"")
            bash_lines.append(f"read -p \"   Proceed with merge for {item['name']}? [y/n]: \" answer")
            bash_lines.append("if [[ \"$answer\" != \"y\" ]]; then echo \"   [SKIPPED] User aborted quarantine merge.\"; continue; fi")

        bash_lines.append(f"mkdir -p \"{dst_dir_bash}\"")
        bash_lines.append(f"if [ -f \"{dst_bash}\" ]; then mkdir -p \"{backup_dir_bash}\"; cp -f \"{dst_bash}\" \"{backup_bash}\"; fi")
        bash_lines.append(f"cp -f \"{src_bash}\" \"{dst_bash}\"")
        bash_lines.append("")

        rollback = {
            "if_destination_existed": "restore_backup",
            "if_destination_missing_before_merge": "delete_destination",
            "backup_path": str(backup_path),
        }
        manifest_operations.append(
            {
                "operation": "copy",
                "kind": "merge_candidate",
                "source_project": source_project,
                "candidate": item.get("name"),
                "wave": item.get("wave"),
                "source": src_for_script,
                "destination": dst_abs,
                "backup_path": str(backup_path),
                "rollback": rollback,
                "post_validation": mutation_post_validation,
                "risk": "quarantine" if is_quarantine else "planned",
                "impact_score": impact_score,
                "evidence": {
                    "source_path": rel_source,
                    "target_path": rel_target,
                    "merge_readiness": item.get("merge_readiness"),
                    "confidence": item.get("confidence"),
                },
            }
        )
        merge_count += 1

    # Hydrate full sanctuary closure after base migration staging.
    hydration = _hydrate_sanctuary_dependency_closure(
        migrations=migrations,
        projects=projects,
        sanitizer_by_project=healer_cache,
        violations_by_project_file=file_violations_index,
    )
    logger.info(
        "[SANCTUARY] Closure hydration complete: copied=%s discovered=%s unresolved_aliases=%s",
        hydration["copied_to_sanctuary"],
        hydration["closure_discovered"],
        hydration["unresolved_aliases"],
    )

    if merge_count == 0:
        non_comparative = not workspace_mode.get("comparative_enabled")
        reason = (
            "Comparative donor migrations are not applicable in this workspace."
            if non_comparative
            else "No eligible low/medium-risk migrations were generated."
        )
        ps_lines.append(f"Write-Host '{reason}'")
        bash_lines.append(f"echo '{reason}'")
        if non_comparative:
            mode_line = "Auto-Merge is operating in non-comparative mode; scripts are emitted as no-op unless local migrations are explicitly proven."
            ps_lines.append(f"Write-Host '{mode_line}'")
            bash_lines.append(f"echo '{mode_line}'")
    else:
        ps_lines.append(f"Write-Host 'Auto-Merge plan generated with {merge_count} file migrations.' -ForegroundColor Green")
        bash_lines.append(f"echo 'Auto-Merge plan generated with {merge_count} file migrations.'")

    ps_file = SCRIPTS_DIR / "auto_merge.ps1"
    sh_file = SCRIPTS_DIR / "auto_merge.sh"
    manifest_file = SCRIPTS_DIR / "auto_merge_manifest.json"
    rollback_file = SCRIPTS_DIR / "auto_merge_rollback_manifest.json"
    ps_text = "\n".join(ps_lines)
    sh_text = "\n".join(bash_lines)
    script_entries = [
        {"name": "auto_merge.ps1", "sha256": _sha256_text(ps_text)},
        {"name": "auto_merge.sh", "sha256": _sha256_text(sh_text)},
    ]

    save_json_atomic(
        manifest_file,
        {
            "meta": {
                "kind": "auto_merge_manifest",
                "version": "v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "guard_env": "CODEMAPS_ALLOW_MUTATION_SCRIPTS",
                "rollback_manifest": "auto_merge_rollback_manifest.json",
                "post_validation": mutation_post_validation,
            },
            "scripts": script_entries,
            "summary": {
                "operation_count": len(manifest_operations),
                "planned_migrations": merge_count,
                "skipped_migrations": skipped_count,
            },
            "operations": manifest_operations,
        },
    )
    save_json_atomic(
        rollback_file,
        {
            "meta": {
                "kind": "auto_merge_rollback_manifest",
                "version": "v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_manifest": "auto_merge_manifest.json",
            },
            "operations": [
                {
                    "destination": op.get("destination"),
                    "backup_path": op.get("backup_path"),
                    "rollback": op.get("rollback"),
                }
                for op in manifest_operations
            ],
        },
    )
    save_text_atomic(ps_file, ps_text)
    save_text_atomic(sh_file, sh_text)

    logger.info(
        f"Auto-Merge scripts generated ({merge_count} physical file migrations planned, {skipped_count} skipped)"
    )
    if not workspace_mode.get("comparative_enabled"):
        logger.info("Auto-Merge is operating in non-comparative mode; scripts are emitted as no-op unless local migrations are explicitly proven.")
    logger.info(f"   PowerShell: {to_posix_path(ps_file.relative_to(SCRIPTS_DIR.parent))}")
    logger.info(f"   Bash: {to_posix_path(sh_file.relative_to(SCRIPTS_DIR.parent))}")

    workspace_confidence = _confidence_from_metrics(collection_metrics)
    by_source_payload = {}
    for project_name, metrics in sorted(by_source_metrics.items()):
        payload_metrics = dict(metrics)
        payload_metrics["planned"] = sum(1 for item in migrations if item.get("source_project") == project_name)
        by_source_payload[project_name] = {
            "summary": payload_metrics,
            "confidence": _confidence_from_metrics(payload_metrics),
        }

    summary_payload = {
        "meta": {"kind": "merge_plan_summary", "version": "v1"},
        "workspace_mode": workspace_mode,
        "summary": {
            "planned_migrations": merge_count,
            "skipped_migrations": skipped_count,
            "sanctuary_hydration": hydration,
            **collection_metrics,
            "confidence": workspace_confidence,
        },
        "by_source_project": by_source_payload,
    }
    save_json_atomic(RAW_DIR / "merge_plan_summary.json", summary_payload)

    md_lines = [
        "# Merge Plan Summary",
        "",
        f"- Planned migrations: `{merge_count}`",
        f"- Skipped migrations: `{skipped_count}`",
        f"- Workspace confidence: `{workspace_confidence['score']}` (`{workspace_confidence['tier']}`)",
        "",
        "## By Source Project",
        "",
        "| Source Project | Planned | Matched | Auto Ready | Confidence | Tier |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for project_name, payload in by_source_payload.items():
        sm = payload.get("summary", {})
        conf = payload.get("confidence", {})
        md_lines.append(
            f"| `{project_name}` | {int(sm.get('planned', 0) or 0)} | {int(sm.get('matched_candidates', 0) or 0)} | {int(sm.get('auto_merge_ready', 0) or 0)} | {float(conf.get('score', 0.0)):.3f} | `{conf.get('tier', 'unknown')}` |"
        )
    save_text_atomic(REPORTS_DIR / "merge_plan_summary.md", "\n".join(md_lines) + "\n")

    return True


if __name__ == "__main__":
    run_merge_script_generator()
