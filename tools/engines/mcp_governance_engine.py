import os
import re
import sys
import json
import base64
import hashlib
import uuid
from pathlib import Path
from typing import Dict, List, Any

# Sovereign Root Recovery (v19.7)
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.core.config import CODE_MAPS_DIR, ROOT, RAW_DIR, DOCTRINE, PRIMARY_ALIAS
from tools.core.decision_ownership import find_owned_decision_copies
from tools.core.layer_resolver import resolve_layer, is_violation
from tools.core.pipeline_policy import get_api_entry_filenames, get_module_root_name, resolve_loc_finding_semantics
from tools.core.logger import logger
from tools.core.audit_rules import build_rule_taxonomy, canonical_alias_boundary_decision
from tools.core.polyglot_imports import extract_imports
from tools.core.language_registry import (
    language_for_extension,
    observation_only_extension_language_map,
)
from tools.core.subprocess_telemetry import run_observed_subprocess
from tools.core.source_files import count_source_lines
from tools.core.doctrine_contract import remediation_action

def _resolve_target_inside_root(target_file: str, workspace_root: str | Path | None = None) -> Path:
    root = Path(workspace_root).resolve() if workspace_root else Path(_ROOT).resolve()
    target_path = (root / target_file).resolve()
    try:
        target_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"target_file escapes selected workspace root: {target_file}") from exc
    return target_path

def apply_unified_diff(original: str, diff_text: str) -> str:
    """
    Parses and applies a unified diff patch to the original text.
    If diff_text does not look like a unified diff, it returns diff_text as the full proposed code.
    """
    original_lines = original.splitlines()
    diff_lines = diff_text.splitlines()
    
    is_diff = False
    for line in diff_lines[:10]:
        if line.startswith("@@ -") or line.startswith("--- ") or line.startswith("+++ ") or line.startswith("diff --git"):
            is_diff = True
            break
            
    if not is_diff:
        return diff_text

    result_lines = []
    orig_idx = 0
    
    i = 0
    while i < len(diff_lines):
        line = diff_lines[i]
        if line.startswith("---") or line.startswith("+++") or line.startswith("diff --git"):
            i += 1
            continue
        if line.startswith("@@"):
            match = re.match(r"^@@ -(\d+),?(\d+)? \+(\d+),?(\d+)? @@", line)
            if not match:
                i += 1
                continue
            
            orig_start = int(match.group(1)) - 1
            # Advance orig_idx to the hunk start
            while orig_idx < orig_start and orig_idx < len(original_lines):
                result_lines.append(original_lines[orig_idx])
                orig_idx += 1
                
            i += 1
            while i < len(diff_lines):
                if diff_lines[i].startswith("@@") or diff_lines[i].startswith("diff --git") or diff_lines[i].startswith("--- ") or diff_lines[i].startswith("+++ "):
                    break
                diff_line = diff_lines[i]
                if diff_line.startswith("-"):
                    expected = diff_line[1:]
                    if orig_idx >= len(original_lines) or original_lines[orig_idx] != expected:
                        raise ValueError(
                            f"Unified diff deletion does not match source at line {orig_idx + 1}."
                        )
                    orig_idx += 1
                elif diff_line.startswith("+"):
                    result_lines.append(diff_line[1:])
                elif diff_line.startswith("\\"):
                    i += 1
                    continue
                else:
                    val = diff_line[1:] if diff_line else ""
                    if orig_idx >= len(original_lines) or original_lines[orig_idx] != val:
                        raise ValueError(
                            f"Unified diff context does not match source at line {orig_idx + 1}."
                        )
                    result_lines.append(val)
                    orig_idx += 1
                i += 1
            continue
        i += 1
        
    while orig_idx < len(original_lines):
        result_lines.append(original_lines[orig_idx])
        orig_idx += 1
        
    return "\n".join(result_lines)


def _looks_like_unified_diff(diff_text: str) -> bool:
    for line in str(diff_text or "").splitlines()[:20]:
        if line.startswith("@@ -") or line.startswith("--- ") or line.startswith("+++ ") or line.startswith("diff --git"):
            return True
    return False


def _unified_diff_has_hunk_change(diff_text: str) -> bool:
    in_hunk = False
    for line in str(diff_text or "").splitlines():
        if line.startswith("@@ "):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            return True
    return False


def _same_logical_content(left: str, right: str) -> bool:
    """Compare patch output without treating line-ending normalization as a real edit."""

    return str(left or "").splitlines() == str(right or "").splitlines()


def _import_violation_key(violation: dict[str, Any]) -> tuple[str, str] | None:
    import_path = str(violation.get("import_path") or "").strip()
    if not import_path:
        return None
    return (str(violation.get("rule") or ""), import_path)


def _collect_import_violations(target_file: str, content: str, language: str) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    source_layer = resolve_layer(target_file)
    module_root_name = get_module_root_name()
    module_import_prefix = f"{PRIMARY_ALIAS}{module_root_name}/"
    current_mod = None
    parts = target_file.replace("\\", "/").split("/")
    if module_root_name in parts:
        idx = parts.index(module_root_name)
        if idx + 1 < len(parts):
            current_mod = parts[idx + 1]

    for imp in extract_imports(content, language):
        target_rel_path = imp.replace(PRIMARY_ALIAS, "") if imp.startswith(PRIMARY_ALIAS) else imp
        target_layer = resolve_layer(target_rel_path)
        is_violated = is_violation(source_layer, target_layer, language=language)
        if not is_violated:
            is_violated = is_violation(
                source_layer.split("/")[0],
                target_layer.split("/")[0],
                language=language,
            )
        if is_violated:
            violations.append(
                {
                    "rule": "hexagonal_layer_violation",
                    "import_path": imp,
                    "detail": f"[{language}] Architectural drift detected: '{source_layer}' layer is forbidden to import from '{target_layer}' layer ({imp}).",
                    "recommended_action": f"Remove {imp} from '{source_layer}'. Rely on pure port interfaces, state stores, or public api bounds.",
                }
            )

        if current_mod and module_import_prefix in imp:
            imp_parts = imp.split(module_import_prefix)[1].split("/")
            if imp_parts:
                imp_mod = imp_parts[0]
                if imp_mod != current_mod:
                    if not imp.endswith("/api") and not imp.endswith("/api/"):
                        violations.append(
                            {
                                "rule": "deep_imports",
                                "import_path": imp,
                                "detail": f"Modular Monolith Leak: File imports internal asset from '{imp_mod}' directly ({imp}). Cross-module imports must go through the module's stable API boundary.",
                                "recommended_action": (
                                    "Do not prescribe a replacement import until the target repository's native "
                                    "boundary policy accepts it. Consider a module public API only when that oracle "
                                    "permits cross-module API imports; otherwise evaluate a consumer-owned port, "
                                    "shared-kernel contract or composition-root injection."
                                ),
                            }
                        )
                else:
                    api_entries = get_api_entry_filenames()
                    is_api_entry = any(target_file.endswith(f"/api/{name}") for name in api_entries)
                    if "/api" in imp and not is_api_entry:
                        violations.append(
                            {
                                "rule": "own_api_imports",
                                "import_path": imp,
                                "detail": f"Internal Loop: Non-API component imports from its own public API entry path ({imp}). This risks circular dependencies.",
                                "recommended_action": "Change the import to refer to the internal widgets/features paths directly instead of going through the public api slot.",
                            }
                        )

        import_restrictions = DOCTRINE.get("architectural_integrity_rules", {}).get("layer_import_restrictions", [])
        for restriction in import_restrictions:
            res_layer = restriction.get("layer")
            res_lang = restriction.get("language")
            if source_layer == res_layer and (not res_lang or res_lang == language):
                for sub in restriction.get("forbidden_substrings", []):
                    aliased_sub = f"{PRIMARY_ALIAS}{sub}"
                    if sub in imp.lower() or aliased_sub in imp.lower():
                        rule_key = restriction.get("rule_key")
                        violations.append(
                            {
                                "rule": rule_key,
                                "import_path": imp,
                                "detail": f"Impure {source_layer} Layer: File imports forbidden framework asset '{imp}' inside layer '{source_layer}'.",
                                "recommended_action": remediation_action(str(rule_key)),
                            }
                        )

        alias_decision = canonical_alias_boundary_decision(
            target_file,
            imp,
            language=language,
            primary_alias=PRIMARY_ALIAS,
            module_root_name=module_root_name,
            alias_contract_applies=bool(PRIMARY_ALIAS),
        )
        if alias_decision.get("violated"):
            violations.append(
                {
                    "rule": "relative_imports_no_alias",
                    "import_path": imp,
                    "detail": f"Canonical Alias Boundary Bypass: local import '{imp}' crosses the safe relative boundary.",
                    "recommended_action": remediation_action("relative_imports_no_alias"),
                    "evidence": alias_decision,
                }
            )
    return violations


def _symbol_loc(sym: dict[str, Any]) -> int | None:
    """Return symbol LOC using line coordinates when available.

    JS/TS sequencers expose `start`/`end` as character offsets and `line`/`endLine`
    as line coordinates. Python/other sequencers may use `start`/`end` as lines.
    """

    start_line = sym.get("line")
    end_line = sym.get("endLine") or sym.get("end_line")
    if start_line is not None and end_line is not None:
        return int(end_line) - int(start_line) + 1
    start = sym.get("start")
    end = sym.get("end")
    if start is None or end is None:
        return None
    return int(end) - int(start) + 1


def _symbol_line_bounds(sym: dict[str, Any]) -> tuple[int | None, int | None]:
    start_line = sym.get("line")
    end_line = sym.get("endLine") or sym.get("end_line")
    if start_line is not None and end_line is not None:
        return int(start_line), int(end_line)
    start = sym.get("start")
    end = sym.get("end")
    if start is None or end is None:
        return None, None
    return int(start), int(end)


def _sequence_existing_symbols(existing_content: str, file_ext: str) -> list[dict[str, Any]]:
    """Sequence current source once for all pre-change governance families."""

    if not existing_content:
        return []
    temp_dir = RAW_DIR
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = temp_dir / f"temp_mcp_existing_{uuid.uuid4().hex}{file_ext}"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(existing_content)
        symbols = run_ast_sequencer(temp_file, file_ext)
    except Exception as exc:
        logger.debug(f"Existing governance baseline sequencing error: {exc}")
        return []
    finally:
        try:
            if temp_file.exists():
                os.remove(temp_file)
        except OSError as exc:
            logger.warning(f"Existing governance baseline temp cleanup failed: {exc}")
    return [row for row in symbols if isinstance(row, dict)]


def _existing_loc_violation_keys(symbols: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Return LOC violations already present before the proposed patch."""

    keys: set[tuple[str, str]] = set()
    for sym in symbols:
        if not isinstance(sym, dict):
            continue
        sym_type = str(sym.get("type", "unknown")).lower()
        sym_name = str(sym.get("name", "unknown"))
        sym_loc = _symbol_loc(sym)
        if sym_loc is None:
            continue
        finding = resolve_loc_finding_semantics(sym_type, sym_loc, symbol_name=sym_name)
        if sym_loc > finding["limit"]:
            keys.add((finding["rule"], sym_name))
    return keys


def _feature_tag_violations(
    symbols: list[dict[str, Any]],
    rule_profiles: dict[str, Any],
) -> list[dict[str, Any]]:
    all_features: set[str] = set()
    for symbol in symbols:
        all_features.update(str(item) for item in (symbol.get("features") or []) if str(item))

    findings: list[dict[str, Any]] = []
    tag_rules = DOCTRINE.get("architectural_integrity_rules", {}).get("feature_tag_audit_rules", [])
    for rule in tag_rules:
        tag_name = str(rule.get("tag") or "")
        rule_key = str(rule.get("rule_key") or "")
        profile = rule_profiles.get(rule_key, {}) if isinstance(rule_profiles, dict) else {}
        rule_mode = str(profile.get("mode") or "disabled").strip().lower()
        if not tag_name or not rule_key or rule_mode == "disabled" or tag_name not in all_features:
            continue
        findings.append(
            {
                "rule": rule_key,
                "rule_mode": rule_mode,
                "detail": f"File uses governed technology tag '{tag_name}'.",
                "recommended_action": remediation_action(rule_key),
                "evidence": {"kind": "feature_tag", "tag": tag_name},
            }
        )
    return findings


def _feature_tag_violation_key(violation: dict[str, Any]) -> tuple[str, str] | None:
    evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
    if evidence.get("kind") != "feature_tag":
        return None
    rule = str(violation.get("rule") or "").strip()
    tag = str(evidence.get("tag") or "").strip()
    return (rule, tag) if rule and tag else None


def _loc_violation_key(violation: dict[str, Any]) -> tuple[str, str] | None:
    rule = str(violation.get("rule") or "")
    if not rule.startswith("loc_limits_"):
        return None
    symbol_name = str(violation.get("symbol_name") or "").strip()
    if symbol_name:
        return (rule, symbol_name)
    detail = str(violation.get("detail") or "")
    match = re.search(r"Symbol '([^']+)'", detail)
    if not match:
        return None
    return (rule, match.group(1))


def run_ast_sequencer(temp_path: Path, file_ext: str) -> List[Dict[str, Any]]:
    """
    Runs the corresponding AST sequencer on the temp file and returns symbol metadata.
    """
    abs_temp_posix = temp_path.resolve().as_posix()
    language = language_for_extension(file_ext)
    
    if language in {"typescript", "javascript"}:
        js_engine = Path(_ROOT) / "tools" / "engines" / "ast_sequencer.cjs"
        doctrine_file = Path(_ROOT) / "config" / "architecture_doctrine.json"
        
        cmd = ["node", str(js_engine)]
        if doctrine_file.exists():
            cmd.extend(["--doctrine-json", str(doctrine_file)])
        cmd.append(abs_temp_posix)
        
        try:
            safe_env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP"}}
            from tools.core.artifact_store import get_adaptive_timeout
            timeout_seconds = get_adaptive_timeout(180)
            res, duration = run_observed_subprocess(
                cmd,
                cwd=Path(_ROOT),
                label="mcp_governance_ast_sequence",
                env=safe_env,
                timeout=timeout_seconds,
                log=logger.info,
            )
            if res.returncode == 0 and res.stdout.strip():
                return json.loads(res.stdout.strip())
            logger.debug(
                "MCP governance AST sequencing returned rc=%s duration_seconds=%.3f timeout_seconds=%s",
                res.returncode,
                duration,
                timeout_seconds,
            )
        except Exception as e:
            logger.debug(f"JS/TS sequencing error: {e}")
            
    elif language == "python":
        try:
            from tools.engines.ast_sequencer_python import sequence_python_file
            return sequence_python_file(str(temp_path))
        except Exception as e:
            logger.debug(f"Python native sequencing error: {e}")
            
    elif language == "go":
        try:
            from tools.engines.ast_sequencer_go import sequence_go_file
            return sequence_go_file(str(temp_path))
        except Exception as e:
            logger.debug(f"Go sequencing error: {e}")
            
    elif language == "java":
        try:
            from tools.engines.ast_sequencer_java import sequence_java_file
            return sequence_java_file(str(temp_path))
        except Exception as e:
            logger.debug(f"Java sequencing error: {e}")
            
    elif language == "csharp":
        try:
            from tools.engines.ast_sequencer_cs import sequence_cs_file
            return sequence_cs_file(str(temp_path))
        except Exception as e:
            logger.debug(f"C# sequencing error: {e}")
            
    return []

def validate_proposed_patch(target_file: str, patch_content: str, workspace_root: str | Path | None = None) -> Dict[str, Any]:
    """
    Main entry point for codemaps.validate_patch tool.
    Applies the patch, runs AST sequencing, and checks constraints against the Architecture Doctrine.
    """
    try:
        target_path = _resolve_target_inside_root(target_file, workspace_root=workspace_root)
    except ValueError as exc:
        return {
            "status": "FAIL",
            "violations": [{
                "rule": "mcp_path_escape",
                "detail": str(exc),
                "recommended_action": "Use a workspace-relative target_file that stays inside the selected target repository root."
            }],
            "metrics": {"loc": 0, "symbols_analyzed": 0}
        }
    file_ext = target_path.suffix.lower()
    language = language_for_extension(file_ext)
    observation_only_language = observation_only_extension_language_map().get(file_ext)
    if language == "unknown":
        observed_as = observation_only_language or "unknown"
        return {
            "status": "INCOMPLETE_EVIDENCE",
            "safe_to_apply": False,
            "unsupported_language": True,
            "language": observed_as,
            "extension": file_ext,
            "violations": [{
                "rule": "unsupported_language_extension",
                "detail": (
                    f"SAGE cannot validate '{file_ext or '<no extension>'}' as an active source "
                    f"language. Observed language classification: '{observed_as}'."
                ),
                "recommended_action": (
                    "Do not treat this result as validation. Use a supported source language "
                    "adapter or obtain an authorized capability extension before mutation."
                ),
            }],
            "metrics": {"loc": 0, "symbols_analyzed": 0},
        }
    
    # 1. Read existing content if present
    existing_content = ""
    if target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
                existing_content = f.read()
        except Exception as e:
            logger.warning(f"Could not read existing file {target_file}: {e}")

    # 2. Apply Unified Diff / Raw Code
    patch_text = str(patch_content or "")
    if _looks_like_unified_diff(patch_text) and not _unified_diff_has_hunk_change(patch_text):
        return {
            "status": "FAIL",
            "safe_to_apply": False,
            "invalid_patch": True,
            "no_op_patch": False,
            "violations": [{
                "rule": "mcp_invalid_patch_format",
                "detail": "Patch text looks like a unified diff but contains no hunk-level additions or removals.",
                "recommended_action": "Provide a complete unified diff with @@ hunks and changed lines, or provide full replacement file content."
            }],
            "metrics": {"loc": count_source_lines(existing_content), "symbols_analyzed": 0}
        }
    try:
        proposed_content = apply_unified_diff(existing_content, patch_content)
    except ValueError as exc:
        return {
            "status": "FAIL",
            "safe_to_apply": False,
            "invalid_patch": True,
            "no_op_patch": False,
            "violations": [{
                "rule": "mcp_patch_source_mismatch",
                "detail": str(exc),
                "recommended_action": "Refresh the target source and regenerate the patch against the current snapshot."
            }],
            "metrics": {"loc": count_source_lines(existing_content), "symbols_analyzed": 0}
        }
    if patch_text.strip() and target_path.exists() and _same_logical_content(proposed_content, existing_content):
        return {
            "status": "NO_OP",
            "safe_to_apply": False,
            "invalid_patch": False,
            "no_op_patch": True,
            "violations": [{
                "rule": "mcp_no_effect_patch",
                "detail": "Patch validation produced no content change for the target file.",
                "recommended_action": "Provide a patch that changes the target file, or skip validate_patch until a concrete edit exists."
            }],
            "metrics": {"loc": count_source_lines(existing_content), "symbols_analyzed": 0}
        }
    
    # Ensure temporary directory exists
    temp_dir = RAW_DIR
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = temp_dir / f"temp_mcp_validate_{uuid.uuid4().hex}{file_ext}"
    
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(proposed_content)
    except Exception as e:
        return {
            "status": "FAIL",
            "violations": [{
                "rule": "mcp_io_failure",
                "detail": f"Failed to write temporary file for validation: {str(e)}",
                "recommended_action": "Ensure workspace permissions permit writing to output/.raw/"
            }],
            "metrics": {"loc": count_source_lines(proposed_content), "symbols_analyzed": 0}
        }
        
    # 3. Parse via AST Sequencer
    symbols = run_ast_sequencer(temp_file, file_ext)
    
    # Clean up temp file safely
    try:
        if temp_file.exists():
            os.remove(temp_file)
    except Exception:
        pass

    violations = []
    
    f_lang = language_for_extension(file_ext)
    
    # 4. Check Architecture Doctrine Constraints
    
    # Rule 1: Complexity Checks (LOC limits)
    for sym in symbols:
        if not isinstance(sym, dict):
            continue
        sym_type = str(sym.get("type", "unknown")).lower()
        sym_name = sym.get("name", "unknown")
        
        sym_loc = _symbol_loc(sym)
        if sym_loc is not None:
            start_line, end_line = _symbol_line_bounds(sym)
            finding = resolve_loc_finding_semantics(
                sym_type,
                sym_loc,
                symbol_name=sym_name,
                start_line=start_line,
                end_line=end_line,
            )
            if sym_loc > finding["limit"]:
                violations.append({
                    **finding,
                    "detail": f"Symbol '{sym_name}' of type '{sym_type}' exceeds standard complexity: {sym_loc} lines (Policy limit: {finding['limit']} lines).",
                    "recommended_action": f"Refactor '{sym_name}' along evidenced responsibilities while preserving its public contract (<{finding['limit']} LOC)."
                })

    # Rule 2: Technology purity uses the same enabled/mode authority as Audit.
    rule_profiles = build_rule_taxonomy().get("profiles", {})
    proposed_feature_violations = _feature_tag_violations(symbols, rule_profiles)
    violations.extend(proposed_feature_violations)

    # Rule 3: Hexagonal Layer and Import Restrictions
    proposed_import_violations = _collect_import_violations(target_file, proposed_content, f_lang)
    existing_import_violations = (
        _collect_import_violations(target_file, existing_content, f_lang)
        if existing_content
        else []
    )
    violations.extend(proposed_import_violations)

    try:
        sage_relative_path = str(target_path.relative_to(CODE_MAPS_DIR.resolve())).replace("\\", "/")
    except ValueError:
        sage_relative_path = ""
    existing_decision_keys: set[tuple[str, str, tuple[str, ...]]] = set()
    if sage_relative_path:
        existing_decision_keys = {
            (
                str(finding["domain"]),
                str(finding["owner_pointer"]),
                tuple(str(item) for item in finding["local_values"]),
            )
            for finding in find_owned_decision_copies(sage_relative_path, existing_content)
        }
        for finding in find_owned_decision_copies(sage_relative_path, proposed_content):
            violations.append(
                {
                    "rule": finding["rule"],
                    "detail": (
                        f"Decision set at line {finding['line']} reconstructs canonical domain "
                        f"'{finding['domain']}' ({finding['relationship']}). Owner: "
                        f"{finding['owner_file']}:{finding['owner_pointer']}."
                    ),
                    "recommended_action": finding["consumer_rule"],
                    "evidence": finding,
                }
            )

    existing_symbols = _sequence_existing_symbols(existing_content, file_ext)
    existing_loc_keys = _existing_loc_violation_keys(existing_symbols)
    existing_feature_violations = _feature_tag_violations(existing_symbols, rule_profiles)
    existing_feature_by_key = {
        key: violation
        for violation in existing_feature_violations
        if (key := _feature_tag_violation_key(violation)) is not None
    }
    proposed_feature_keys = {
        key
        for violation in proposed_feature_violations
        if (key := _feature_tag_violation_key(violation)) is not None
    }
    existing_import_by_key = {
        key: violation
        for violation in existing_import_violations
        if (key := _import_violation_key(violation)) is not None
    }
    proposed_import_keys = {
        key
        for violation in proposed_import_violations
        if (key := _import_violation_key(violation)) is not None
    }
    resolved_violations = [
        {**violation, "baseline_status": "resolved_by_patch"}
        for key, violation in existing_import_by_key.items()
        if key not in proposed_import_keys
    ]
    resolved_violations.extend(
        {**violation, "baseline_status": "resolved_by_patch"}
        for key, violation in existing_feature_by_key.items()
        if key not in proposed_feature_keys
    )
    blocking_violations: list[dict[str, Any]] = []
    advisory_violations: list[dict[str, Any]] = []
    existing_violations: list[dict[str, Any]] = []
    for violation in violations:
        import_key = _import_violation_key(violation)
        loc_key = _loc_violation_key(violation)
        feature_key = _feature_tag_violation_key(violation)
        evidence = violation.get("evidence") if isinstance(violation.get("evidence"), dict) else {}
        decision_key = (
            str(evidence.get("domain") or ""),
            str(evidence.get("owner_pointer") or ""),
            tuple(str(item) for item in evidence.get("local_values", [])),
        ) if violation.get("rule") == "central_decision_reconstructed_locally" else None
        if feature_key and str(violation.get("rule_mode") or "").strip().lower() != "enforced":
            baseline_status = (
                "pre_existing_non_blocking"
                if feature_key in existing_feature_by_key
                else "introduced_advisory"
            )
            advisory_violations.append({**violation, "baseline_status": baseline_status})
            continue
        if (
            (import_key and import_key in existing_import_by_key)
            or (loc_key and loc_key in existing_loc_keys)
            or (feature_key and feature_key in existing_feature_by_key)
            or (decision_key and decision_key in existing_decision_keys)
        ):
            existing_violations.append({**violation, "baseline_status": "pre_existing_non_blocking"})
        else:
            blocking_violations.append(violation)

    return {
        "status": "PASS" if not blocking_violations else "FAIL",
        "violations": blocking_violations,
        "introduced_violations": blocking_violations,
        "existing_violations": existing_violations,
        "unchanged_violations": existing_violations,
        "advisory_violations": advisory_violations,
        "resolved_violations": resolved_violations,
        "existing_violation_count": len(existing_violations),
        "introduced_violation_count": len(blocking_violations),
        "advisory_violation_count": len(advisory_violations),
        "resolved_violation_count": len(resolved_violations),
        "unchanged_violation_count": len(existing_violations),
        "metrics": {
            "loc": count_source_lines(proposed_content),
            "symbols_analyzed": len(symbols)
        }
    }
