import os
import json
import re
from typing import List, Dict, Tuple
from pathlib import Path
from tools.core.config import (
    SANCTUARY_DIR,
    ROOT,
    LOGS_DIR,
    DYNAMIC_CONFIG,
    MAIN_PROJECT_ROOT,
    DOCTRINE,
    CODE_MAPS_DIR,
    normalize_path,
    save_json_atomic,
    save_text_atomic,
)
from tools.core.logger import logger
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.core.jsonc import loads_jsonc
from tools.core.subprocess_telemetry import run_observed_subprocess
from tools.core.target_repository_trust import (
    is_target_path_contained,
    load_target_repository_threat_boundary_contract,
)

SUSPICIOUS_JSX_PATTERNS = [
    (
        re.compile(r'@/\s*>'),
        "Malformed JSX token fragment detected.",
        "malformed_jsx_token",
        "high",
    ),
]
TSC_ERROR_CLASSES = {
    "deep_tsc_error",
    "environment_type_error",
    "jsx_intrinsic_contract_error",
    "missing_declared_dependency_in_sanctuary",
    "missing_package_declaration",
    "module_augmentation_error",
    "typescript_strictness_error",
    "prop_contract_error",
    "type_contract_error",
}
SYNTAX_ERROR_CLASSES = {"deep_tsc_parser_error", "malformed_jsx_token", *TSC_ERROR_CLASSES}
RESOLUTION_ERROR_CLASSES = {
    "host_missing_alias",
    "legacy_src_alias_mismatch",
    "relocated_alias_candidate",
    "sanctuary_snapshot_gap",
}


def _iter_files_under(root: Path | str, extensions: tuple[str, ...] | set[str] | list[str]) -> List[Path]:
    base = Path(root)
    if not base.exists():
        return []
    suffixes = tuple(str(ext).lower() for ext in extensions)
    files: List[Path] = []
    for current_root, dirs, names in os.walk(str(base)):
        dirs[:] = [
            name
            for name in dirs
            if name not in {"node_modules", ".git", "dist", "build", ".next", "coverage", "__pycache__"}
        ]
        for name in names:
            path = Path(current_root) / name
            if path.suffix.lower() in suffixes and is_target_path_contained(base, path):
                files.append(path)
    return files

class ValidationOracle:
    """
    Autonomous Architectural Quality Gate.
    Executes structural checks in the Sanctuary and parses errors to identify broken mappings.
    """
    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root)
        self.sanctuary_root = SANCTUARY_DIR

    def validate_project(self, project_name: str) -> Dict:
        """Runs validation checks on a specific quarantined project in the Sanctuary."""
        project_sanctuary = os.path.join(self.sanctuary_root, project_name)
        if not os.path.exists(project_sanctuary):
            report = {
                "project": project_name,
                "status": "NOT_APPLICABLE",
                "broken_imports": [],
                "oracle_summary": {
                    "total": 0,
                    "deep_tsc_errors": 0,
                    "syntax_errors": 0,
                    "alias_errors": 0,
                    "resolution_errors": 0,
                    "reasons": {},
                    "scopes": {},
                    "classifications": {},
                    "severities": {},
                },
                "sanctuary_hydration": {
                    "added_files": 0,
                    "unresolved_aliases": 0,
                },
                "sanctuary_path": project_sanctuary,
                "message": f"Sanctuary for {project_name} not found.",
            }
            self._save_report(project_name, report)
            return report

        logger.info(f"[ORACLE] Starting autonomous semantic validation for {project_name}...")
        
        try:
            preflight = self.preflight_project(project_name)
            if not preflight["applicable"]:
                report = {
                    "project": project_name,
                    "status": "NOT_APPLICABLE",
                    "broken_imports": [],
                    "oracle_summary": {
                        "total": 0,
                        "deep_tsc_errors": 0,
                        "syntax_errors": 0,
                        "alias_errors": 0,
                        "resolution_errors": 0,
                        "reasons": {},
                        "scopes": {},
                        "classifications": {},
                        "severities": {},
                    },
                    "sanctuary_hydration": {"added_files": 0, "unresolved_aliases": 0, "skipped": True},
                    "sanctuary_path": project_sanctuary,
                    "preflight": preflight,
                    "message": str(preflight.get("reason") or "Validation Oracle is not applicable."),
                }
                self._save_report(project_name, report)
                logger.info("[ORACLE] Preflight skipped %s: %s", project_name, report["message"])
                return report

            hydration = self._hydrate_sanctuary_from_variation(project_name, project_sanctuary)

            # 1. Structural Linkage Scan (Fast)
            errors = self._scan_for_broken_imports(project_name, project_sanctuary)
            if any(str(item.get("classification") or "") == "sanctuary_snapshot_gap" for item in errors):
                targeted = self._hydrate_missing_aliases(
                    project_name,
                    project_sanctuary,
                    [
                        str(item.get("import") or "")
                        for item in errors
                        if str(item.get("classification") or "") == "sanctuary_snapshot_gap"
                    ],
                )
                hydration["targeted_added_files"] = int(targeted.get("added_files", 0) or 0)
                hydration["targeted_unresolved_aliases"] = int(targeted.get("unresolved_aliases", 0) or 0)
                if hydration["targeted_added_files"] > 0:
                    errors = self._scan_for_broken_imports(project_name, project_sanctuary)

            # 1b. Lightweight syntax pre-scan catches obvious JSX/token corruption before TSC.
            errors.extend(self._scan_for_suspicious_syntax(project_sanctuary))
              
            # 2. Deep TSC Validation (Fidelity)
            tsc_errors = self._run_tsc_validation(
                project_name,
                project_sanctuary,
                node_modules_root=Path(str(preflight["node_modules_root"])),
                command=list(preflight["command"]),
            )
            errors.extend(tsc_errors)
            errors = self._dedupe_errors(errors)
            
            summary = self._summarize_errors(errors)
            report = {
                "project": project_name,
                "status": self._derive_status(errors, summary),
                "broken_imports": errors,
                "oracle_summary": summary,
                "sanctuary_hydration": hydration,
                "sanctuary_path": project_sanctuary,
                "preflight": preflight,
            }
            
            self._save_report(project_name, report)
            return report
            
        except Exception as e:
            logger.error(f"[ORACLE] Validation failed for {project_name}: {e}")
            return {"status": "FAIL", "error": str(e)}

    def preflight_project(self, project_name: str) -> Dict:
        source_root = self._source_root_for_project(project_name)
        node_modules_root = self._resolve_node_modules_root(source_root)
        if not node_modules_root:
            return {
                "applicable": False,
                "reason": "SAGE-owned TypeScript compiler not found; Sanctuary hydration was not started.",
                "source_root": str(source_root),
                "node_modules_root": "",
                "command": [],
                "compiler_authority": "not_available",
                "executes_target_code": False,
            }
        command = self._resolve_tsc_command(node_modules_root)
        if not command:
            return {
                "applicable": False,
                "reason": "local TypeScript executable not found; Sanctuary hydration was not started.",
                "source_root": str(source_root),
                "node_modules_root": str(node_modules_root),
                "command": [],
                "compiler_authority": "not_available",
                "executes_target_code": False,
            }
        return {
            "applicable": True,
            "reason": "local TypeScript compiler available",
            "source_root": str(source_root),
            "node_modules_root": str(node_modules_root),
            "command": command,
            "compiler_authority": "sage_runtime",
            "executes_target_code": False,
            "target_native_binary_resolution": "forbidden",
        }

    def _run_tsc_validation(
        self,
        project_name: str,
        project_dir: str,
        *,
        node_modules_root: Path | None = None,
        command: List[str] | None = None,
    ) -> List[Dict]:
        """Run the SAGE-owned TypeScript compiler over a bounded Sanctuary projection."""
        logger.info(f"[ORACLE] Executing Deep TSC Validation for {project_name}...")

        source_root = self._source_root_for_project(project_name)
        node_modules_root = node_modules_root or self._resolve_node_modules_root(source_root)
        if not node_modules_root:
            logger.warning("[ORACLE] node_modules not found; skipping Deep TSC.")
            return []

        # Resolve and verify the executable authority before materializing transient input.
        cmd = self._resolve_tsc_command(node_modules_root)
        if not cmd:
            logger.warning("[ORACLE] TypeScript executable not found; skipping Deep TSC.")
            return []
        if command is not None and list(command) != cmd:
            logger.error("[ORACLE] Refusing non-canonical TypeScript command for %s.", project_name)
            return []

        tsconfig = self._build_sanctuary_tsconfig(source_root)
        tsconfig_path = Path(project_dir) / "tsconfig.sanctuary.json"
        save_json_atomic(tsconfig_path, tsconfig)

        # Execute the SAGE-owned compiler over the transient bounded projection.
        try:
            full_cmd = [*cmd, "--project", "tsconfig.sanctuary.json", "--noEmit", "--pretty", "false"]
            safe_env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP"}}
            from tools.core.artifact_store import get_adaptive_timeout
            timeout_seconds = get_adaptive_timeout(180)
            result, duration = run_observed_subprocess(
                full_cmd,
                cwd=Path(project_dir),
                label=f"oracle_tsc_{project_name}",
                env=safe_env,
                timeout=timeout_seconds,
                log=logger.info,
            )
            logger.info(
                "[ORACLE] Deep TSC finished for %s rc=%s duration_seconds=%.3f timeout_seconds=%s",
                project_name,
                result.returncode,
                duration,
                timeout_seconds,
            )
            
            if result.returncode == 0:
                logger.info(f"[ORACLE] TSC Validation PASSED for {project_name}")
                return []
            if result.returncode == 124:
                logger.warning("[ORACLE] TSC Validation timed out for %s.", project_name)
                return []
            
            # 3. Parse Errors
            # Errors are typically: path/to/file.ts(line,col): error TSXXXX: Message
            combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
            diagnostics = self._parse_tsc_output(combined, project_dir, project_name)
            if diagnostics:
                logger.warning(f"[ORACLE] TSC Validation found actionable diagnostics in {project_name}")
            else:
                logger.info(
                    f"[ORACLE] TSC returned non-zero for {project_name}, but no actionable diagnostics were parsed."
                )
            return diagnostics
            
        except Exception as e:
            logger.error(f"[ORACLE] TSC execution failed: {e}")
            return []
        finally:
            try:
                os.remove(tsconfig_path)
            except OSError:
                pass

    def _source_root_for_project(self, project_name: str) -> Path:
        """Return the original source root for a configured variation/main project."""
        variations = DYNAMIC_CONFIG.get("variations", {}) or {}
        project_rel = normalize_path(variations.get(project_name))
        if project_rel and project_rel != ".":
            candidate = (ROOT / project_rel).resolve()
        else:
            candidate = MAIN_PROJECT_ROOT.resolve()
        if not is_target_path_contained(ROOT, candidate):
            raise ValueError(f"Configured project root escapes analyzed repository: {project_name}")
        return candidate

    def _resolve_node_modules_root(self, source_root: Path) -> Path | None:
        """Return only the SAGE-owned TypeScript runtime; never a target dependency tree."""
        _ = source_root
        node_modules_root = CODE_MAPS_DIR / "tools" / "engines" / "node_modules"
        compiler = node_modules_root / "typescript" / "bin" / "tsc"
        return node_modules_root if compiler.is_file() else None

    def _build_sanctuary_tsconfig(self, source_root: Path) -> Dict:
        """Build a project-aware transient tsconfig without leaking host files into scope."""
        compiler_options: Dict = {}
        boundary = load_target_repository_threat_boundary_contract().get(
            "typescript_static_compiler_projection",
            {},
        )
        allowed_options = {
            str(value)
            for value in boundary.get("allowed_literal_compiler_options", [])
            if str(value).strip()
        }
        source_tsconfig = source_root / "tsconfig.json"
        if is_target_path_contained(source_root, source_tsconfig) and source_tsconfig.is_file():
            try:
                source_config = loads_jsonc(source_tsconfig.read_text(encoding="utf-8"))
                source_options = source_config.get("compilerOptions")
                if isinstance(source_options, dict):
                    compiler_options.update(
                        {
                            str(key): value
                            for key, value in source_options.items()
                            if str(key) in allowed_options
                            and (
                                isinstance(value, (str, int, float, bool))
                                or isinstance(value, list)
                                and all(isinstance(item, (str, int, float, bool)) for item in value)
                            )
                        }
                    )
            except Exception as exc:
                logger.warning("[ORACLE] Could not read source tsconfig %s: %s", source_tsconfig, exc)

        compiler_options.update(
            {
                "baseUrl": ".",
                "noEmit": True,
                "skipLibCheck": True,
                "jsx": compiler_options.get("jsx", "preserve"),
            }
        )
        compiler_options["paths"] = dict(boundary.get("sanctuary_aliases") or {"@/*": ["./src/*"]})

        oracle_settings = require_doctrine_mapping("validation_oracle_settings")
        default_include = ["src/**/*.ts", "src/**/*.tsx", "src/**/*.js", "src/**/*.jsx"]
        default_exclude = ["node_modules", ".next", "dist", "build"]
        return {
            "compilerOptions": compiler_options,
            "include": oracle_settings.get("include", default_include),
            "exclude": oracle_settings.get("exclude", default_exclude),
        }

    def _resolve_tsc_command(self, node_modules_root: Path | None = None) -> List[str]:
        """Resolve only the direct SAGE-owned TypeScript entry point."""
        root = node_modules_root or self._resolve_node_modules_root(ROOT)
        if root is None:
            return []
        ts_lib = Path(root) / "typescript" / "bin" / "tsc"
        expected = CODE_MAPS_DIR / "tools" / "engines" / "node_modules" / "typescript" / "bin" / "tsc"
        if ts_lib.resolve() != expected.resolve() or not ts_lib.is_file():
            return []
        return ["node", str(ts_lib)]

    def _declared_dependencies_for_project(self, project_name: str) -> set[str]:
        source_root = self._source_root_for_project(project_name)
        package_path = source_root / "package.json"
        if not is_target_path_contained(source_root, package_path) or not package_path.is_file():
            return set()
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[ORACLE] Failed to load package.json for project %s: %s", project_name, exc)
            return set()
        deps: set[str] = set()
        for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            values = package.get(section, {})
            if isinstance(values, dict):
                deps.update(str(name) for name in values.keys())
        return deps

    @staticmethod
    def _module_name_from_tsc_message(message: str) -> str:
        match = re.search(r"Cannot find module ['\"]([^'\"]+)['\"]", str(message or ""))
        if not match:
            return ""
        module_name = match.group(1)
        if module_name.startswith("@"):
            parts = module_name.split("/")
            return "/".join(parts[:2]) if len(parts) >= 2 else module_name
        return module_name.split("/", 1)[0]

    def _parse_tsc_output(self, output: str, project_dir: str, project_name: str) -> List[Dict]:
        raw_errors = []
        declared_dependencies = self._declared_dependencies_for_project(project_name)
        # Pattern: file.ts(1,1): error TS123: Message
        pattern = re.compile(r"(.+?)\((\d+),(\d+)\): error (TS\d+): (.+)")
        for line in output.splitlines():
            match = pattern.match(line)
            if match:
                file_path = match.group(1)
                normalized_file = self._normalize_rel_path(file_path)
                if "node_modules/" in normalized_file:
                    continue
                line_number = match.group(2)
                code = match.group(4)
                message = match.group(5)
                source_line = self._safe_source_line(project_dir, file_path, line_number)
                reason, classification, severity = self._classify_tsc_issue(code, message, source_line)
                dependency_context = None
                module_name = self._module_name_from_tsc_message(message) if code == "TS2307" else ""
                if module_name:
                    dependency_context = {
                        "module": module_name,
                        "declared_in_package_json": module_name in declared_dependencies,
                    }
                    if module_name in declared_dependencies:
                        reason = "Declared dependency is unresolved in sanctuary TypeScript environment."
                        classification = "missing_declared_dependency_in_sanctuary"
                    else:
                        reason = "Package is not declared in source package.json."
                        classification = "missing_package_declaration"
                raw_errors.append({
                    "file": normalized_file,
                    "line": line_number,
                    "code": code,
                    "message": message,
                    "reason": reason,
                    "classification": classification,
                    "severity": severity,
                    "source_line": source_line,
                    "dependency_context": dependency_context,
                })

        aggregated: Dict[Tuple[str, str, str], Dict] = {}
        for item in raw_errors:
            key = (
                str(item.get("file") or ""),
                str(item.get("line") or ""),
                str(item.get("classification") or "deep_tsc_error"),
            )
            current = aggregated.get(key)
            if current is None:
                clone = dict(item)
                clone["codes"] = [str(item.get("code") or "")]
                aggregated[key] = clone
                continue
            current_codes = list(current.get("codes", []) or [])
            code = str(item.get("code") or "")
            if code and code not in current_codes:
                current_codes.append(code)
            current["codes"] = current_codes
            if len(str(item.get("message") or "")) > len(str(current.get("message") or "")):
                current["message"] = item.get("message")
            if str(item.get("severity") or "") == "high":
                current["severity"] = "high"

        for item in aggregated.values():
            codes = [code for code in item.get("codes", []) if code]
            if codes:
                item["code"] = ",".join(codes)
            item.pop("codes", None)
            item.pop("source_line", None)
            if item.get("dependency_context") is None:
                item.pop("dependency_context", None)
        return list(aggregated.values())

    def _safe_source_line(self, project_dir: str, file_path: str, line_number: str) -> str:
        try:
            sanctuary_root = Path(project_dir).resolve()
            target = (sanctuary_root / str(file_path)).resolve()
            if not is_target_path_contained(sanctuary_root, target) or not target.is_file():
                return ""
            number = int(line_number)
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            if number <= 0 or number > len(lines):
                return ""
            return lines[number - 1]
        except Exception as exc:
            logger.warning("[ORACLE] Failed to read source line from %s: %s", file_path, exc)
            return ""

    def _classify_tsc_issue(self, code: str, message: str, source_line: str) -> Tuple[str, str, str]:
        normalized_line = str(source_line or "")
        normalized_message = str(message or "")
        if "@/>" in normalized_line or "\"@/>" in normalized_line:
            return (
                "Malformed JSX token in source line.",
                "malformed_jsx_token",
                "high",
            )
        if code in {"TS1003", "TS1382"} and ("Unexpected token" in normalized_message or "Identifier expected" in normalized_message):
            return (
                "Deep TypeScript parser error.",
                "deep_tsc_parser_error",
                "high",
            )
        if code in {"TS7006", "TS7031"}:
            return ("TypeScript strictness error.", "typescript_strictness_error", "high")
        if code in {"TS2322", "TS2345", "TS2741", "TS2769"}:
            return ("TypeScript prop/value contract error.", "prop_contract_error", "high")
        if code in {"TS2339", "TS2551"} and "JSX.IntrinsicElements" in normalized_message:
            return ("JSX intrinsic element contract error.", "jsx_intrinsic_contract_error", "high")
        if code in {"TS2688", "TS2580", "TS2591"}:
            return ("Environment type package is missing or not visible.", "environment_type_error", "high")
        if code in {"TS2664", "TS2665"} or "Invalid module name in augmentation" in normalized_message:
            return ("Module augmentation contract error.", "module_augmentation_error", "high")
        if code in {"TS2339", "TS2416", "TS2420", "TS2430"}:
            return ("Type/interface contract error.", "type_contract_error", "high")
        return ("Deep TypeScript Validation Error", "deep_tsc_error", "high")

    def _scan_for_broken_imports(self, project_name: str, project_dir: str) -> List[Dict]:
        """Project-aware structural scan for aliases that cannot be resolved safely."""
        broken = []
        import_patterns = [
            re.compile(r"(?:import|export)\s+[^;]*?\sfrom\s*['\"](@/[^'\"]+)['\"]"),
            re.compile(r"import\s*['\"](@/[^'\"]+)['\"]"),
            re.compile(r"import\(\s*['\"](@/[^'\"]+)['\"]\s*\)"),
        ]
        oracle_settings = require_doctrine_mapping("validation_oracle_settings")
        extensions = tuple(oracle_settings.get("extensions", [".ts", ".tsx", ".js", ".jsx"]))
        for full_path in _iter_files_under(project_dir, extensions):
            try:
                content = full_path.read_text(encoding="utf-8")
                # Find @/ aliases only in import/export contexts.
                aliases = []
                for pattern in import_patterns:
                    aliases.extend(pattern.findall(content))
                for alias in list(dict.fromkeys(aliases)):
                    is_resolved, details = self._verify_alias_exists(alias, project_name, project_dir)
                    if not is_resolved:
                        broken.append({
                            "file": os.path.relpath(str(full_path), project_dir),
                            "import": alias,
                            "reason": details.get("reason", "Alias could not be resolved."),
                            "resolution_scope": details.get("scope", "unresolved"),
                            "resolution_detail": details.get("detail", ""),
                            "classification": details.get("classification", "alias_unresolved"),
                            "severity": details.get("severity", "medium"),
                        })
            except Exception as fe:
                logger.warning(f"[ORACLE] Could not read {full_path}: {fe}")
        return broken

    def _scan_for_suspicious_syntax(self, project_dir: str) -> List[Dict]:
        findings: List[Dict] = []
        from tools.core.doctrine_contract import require_doctrine_path
        ui_extensions = tuple(require_doctrine_path("ui_discovery", "extensions", expected_type=list))
        for full_path in _iter_files_under(project_dir, ui_extensions):
            try:
                lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception as exc:
                logger.warning(f"[ORACLE] Could not pre-scan {full_path}: {exc}")
                continue
            for index, line in enumerate(lines, start=1):
                for pattern, reason, classification, severity in SUSPICIOUS_JSX_PATTERNS:
                    if not pattern.search(line):
                        continue
                    findings.append(
                        {
                            "file": self._normalize_rel_path(os.path.relpath(str(full_path), project_dir)),
                            "line": str(index),
                            "code": "PRE_JSX",
                            "message": "Suspicious JSX token pattern found before TypeScript validation.",
                            "reason": reason,
                            "classification": classification,
                            "severity": severity,
                        }
                    )
        return findings

    def _hydrate_sanctuary_from_variation(self, project_name: str, project_dir: str) -> Dict:
        """
        Before validation, pull missing @/ alias dependencies from variation source into sanctuary.
        This keeps Oracle focused on real issues instead of snapshot incompleteness.
        """
        variations = DYNAMIC_CONFIG.get("variations", {}) or {}
        project_rel = normalize_path(variations.get(project_name))
        if not project_rel or project_rel == ".":
            return {"added_files": 0, "unresolved_aliases": 0}

        variation_root = self._source_root_for_project(project_name)
        sanctuary_root = Path(project_dir).resolve()
        if not variation_root.exists() or not sanctuary_root.exists():
            return {"added_files": 0, "unresolved_aliases": 0}

        pending = []
        visited_files = set()
        unresolved_aliases = set()
        added_files = 0

        oracle_settings = require_doctrine_mapping("validation_oracle_settings")
        extensions = tuple(oracle_settings.get("extensions", [".ts", ".tsx", ".js", ".jsx"]))
        pending.extend(_iter_files_under(sanctuary_root, extensions))

        while pending:
            file_path = pending.pop()
            file_key = str(file_path.resolve())
            if file_key in visited_files:
                continue
            visited_files.add(file_key)

            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning("[ORACLE] Failed to read file content for %s: %s", file_path, exc)
                continue

            added_now, unresolved_now, hydrated_paths = self._hydrate_aliases(
                project_name,
                project_dir,
                self._extract_aliases(content),
            )
            added_files += added_now
            unresolved_aliases.update(unresolved_now)
            pending.extend(hydrated_paths)

            relative_added, relative_paths = self._hydrate_relative_imports(
                project_name,
                project_dir,
                file_path,
                self._extract_relative_imports(content),
            )
            added_files += relative_added
            pending.extend(relative_paths)

        if added_files:
            logger.info(
                "[ORACLE] Sanctuary hydration for %s: added_files=%s unresolved_aliases=%s",
                project_name,
                added_files,
                len(unresolved_aliases),
            )
        return {"added_files": added_files, "unresolved_aliases": len(unresolved_aliases)}

    def _hydrate_missing_aliases(self, project_name: str, project_dir: str, aliases: List[str]) -> Dict:
        added_files, unresolved_aliases, _ = self._hydrate_aliases(project_name, project_dir, aliases)
        if added_files:
            logger.info(
                "[ORACLE] Targeted sanctuary hydration for %s: added_files=%s unresolved_aliases=%s",
                project_name,
                added_files,
                len(unresolved_aliases),
            )
        return {"added_files": added_files, "unresolved_aliases": len(unresolved_aliases)}

    def _extract_aliases(self, content: str) -> List[str]:
        import_patterns = [
            re.compile(r"(?:import|export)\s+[^;]*?\sfrom\s*['\"](@/[^'\"]+)['\"]"),
            re.compile(r"import\s*['\"](@/[^'\"]+)['\"]"),
            re.compile(r"import\(\s*['\"](@/[^'\"]+)['\"]\s*\)"),
        ]
        found = []
        for pattern in import_patterns:
            found.extend(pattern.findall(content))
        return list(dict.fromkeys(found))

    def _extract_relative_imports(self, content: str) -> List[str]:
        import_patterns = [
            re.compile(r"(?:import|export)\s+[^;]*?\sfrom\s*['\"]((?:\./|\.\./)[^'\"]+)['\"]"),
            re.compile(r"import\s*['\"]((?:\./|\.\./)[^'\"]+)['\"]"),
            re.compile(r"import\(\s*['\"]((?:\./|\.\./)[^'\"]+)['\"]\s*\)"),
        ]
        found = []
        for pattern in import_patterns:
            found.extend(pattern.findall(content))
        return list(dict.fromkeys(found))

    def _hydrate_relative_imports(
        self,
        project_name: str,
        project_dir: str,
        sanctuary_file: Path,
        imports: List[str],
    ) -> Tuple[int, List[Path]]:
        variations = DYNAMIC_CONFIG.get("variations", {}) or {}
        project_rel = normalize_path(variations.get(project_name))
        if not project_rel or project_rel == ".":
            return 0, []

        variation_root = self._source_root_for_project(project_name)
        sanctuary_root = Path(project_dir).resolve()
        if not variation_root.exists() or not sanctuary_root.exists():
            return 0, []

        try:
            relative_file = sanctuary_file.resolve().relative_to(sanctuary_root)
        except Exception as exc:
            logger.warning("[ORACLE] Sanctuary file relative path calculation failed: %s", exc)
            return 0, []

        source_file = (variation_root / relative_file).resolve()
        if not is_target_path_contained(variation_root, source_file) or not source_file.is_file():
            return 0, []

        added_files = 0
        hydrated_paths: List[Path] = []
        for import_str in list(dict.fromkeys(str(item or "").strip() for item in imports if str(item or "").strip())):
            resolved_var = self._resolve_relative_under_source(
                source_file,
                import_str,
                source_root=variation_root,
            )
            if not resolved_var:
                continue
            try:
                rel_from_variation = resolved_var.relative_to(variation_root)
            except Exception as exc:
                logger.warning("[ORACLE] Variation relative path calculation failed for %s: %s", resolved_var, exc)
                continue
            sanctuary_target = (sanctuary_root / rel_from_variation).resolve()
            if sanctuary_target.exists():
                continue
            sanctuary_target.parent.mkdir(parents=True, exist_ok=True)
            try:
                copied_files, created_paths = self._materialize_variation_path(
                    source_path=resolved_var,
                    destination_path=sanctuary_target,
                    source_root=variation_root,
                    destination_root=sanctuary_root,
                )
            except Exception as exc:
                logger.warning("[ORACLE] Failed to materialize variation path for %s: %s", resolved_var, exc)
                continue
            added_files += copied_files
            hydrated_paths.extend(created_paths)
        return added_files, hydrated_paths

    def _resolve_relative_under_source(
        self,
        source_file: Path,
        import_str: str,
        *,
        source_root: Path,
    ) -> Path | None:
        base = (source_file.parent / import_str).resolve()
        if is_target_path_contained(source_root, base) and base.exists():
            return base
        extensions = [".ts", ".tsx", ".js", ".jsx", ".d.ts", ".json"]
        for ext in extensions:
            candidate = Path(f"{base}{ext}")
            if is_target_path_contained(source_root, candidate) and candidate.exists():
                return candidate
        for index_name in ["index.ts", "index.tsx", "index.js", "index.jsx", "index.d.ts"]:
            candidate = base / index_name
            if is_target_path_contained(source_root, candidate) and candidate.exists():
                return candidate
        return None

    def _hydrate_aliases(self, project_name: str, project_dir: str, aliases: List[str]) -> Tuple[int, set[str], List[Path]]:
        variations = DYNAMIC_CONFIG.get("variations", {}) or {}
        project_rel = normalize_path(variations.get(project_name))
        if not project_rel or project_rel == ".":
            return 0, set(), []

        variation_root = self._source_root_for_project(project_name)
        sanctuary_root = Path(project_dir).resolve()
        if not variation_root.exists() or not sanctuary_root.exists():
            return 0, set(), []

        added_files = 0
        unresolved_aliases: set[str] = set()
        hydrated_paths: List[Path] = []

        for alias in list(dict.fromkeys(str(alias or "").strip() for alias in aliases if str(alias or "").strip())):
            rel_candidates = self._alias_rel_candidates(alias)
            if not rel_candidates:
                continue

            if self._resolve_alias_under_root(sanctuary_root, rel_candidates) or self._resolve_alias_under_root(sanctuary_root / "src", rel_candidates):
                continue

            resolved_var = self._resolve_alias_under_root(variation_root, rel_candidates) or self._resolve_alias_under_root(variation_root / "src", rel_candidates)
            if not resolved_var:
                unresolved_aliases.add(alias)
                continue

            try:
                rel_from_variation = os.path.relpath(str(resolved_var), str(variation_root)).replace("\\", "/")
            except Exception as exc:
                logger.warning("[ORACLE] Alias path calculation failed for %s: %s", resolved_var, exc)
                unresolved_aliases.add(alias)
                continue

            sanctuary_target = (sanctuary_root / rel_from_variation).resolve()
            if sanctuary_target.exists():
                continue

            sanctuary_target.parent.mkdir(parents=True, exist_ok=True)
            try:
                copied_files, created_paths = self._materialize_variation_path(
                    source_path=resolved_var,
                    destination_path=sanctuary_target,
                    source_root=variation_root,
                    destination_root=sanctuary_root,
                )
                if copied_files > 0:
                    added_files += copied_files
                    hydrated_paths.extend(created_paths)
                else:
                    unresolved_aliases.add(alias)
            except Exception as exc:
                logger.warning("[ORACLE] Failed to materialize variation path for alias %s: %s", resolved_var, exc)
                unresolved_aliases.add(alias)

        return added_files, unresolved_aliases, hydrated_paths

    def _materialize_variation_path(
        self,
        source_path: Path,
        destination_path: Path,
        *,
        source_root: Path,
        destination_root: Path,
    ) -> Tuple[int, List[Path]]:
        if (
            not is_target_path_contained(source_root, source_path)
            or not is_target_path_contained(destination_root, destination_path)
        ):
            return 0, []
        if source_path.is_dir():
            copied_files = 0
            created_paths: List[Path] = []
            for child in _iter_files_under(source_path, {".ts", ".tsx", ".js", ".jsx", ".d.ts", ".json"}):
                relative = child.relative_to(source_path)
                target = destination_path / relative
                if not is_target_path_contained(destination_root, target):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                save_text_atomic(target, child.read_text(encoding="utf-8"))
                copied_files += 1
                created_paths.append(target)
            return copied_files, created_paths

        save_text_atomic(destination_path, source_path.read_text(encoding="utf-8"))
        return 1, [destination_path]

    def _dedupe_errors(self, errors: List[Dict]) -> List[Dict]:
        deduped: Dict[Tuple[str, str, str, str], Dict] = {}
        severity_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
        for item in errors:
            key = (
                self._normalize_rel_path(str(item.get("file") or "")),
                str(item.get("line") or ""),
                str(item.get("classification") or ""),
                str(item.get("import") or ""),
            )
            current = deduped.get(key)
            if current is None:
                clone = dict(item)
                clone["file"] = self._normalize_rel_path(str(clone.get("file") or ""))
                deduped[key] = clone
                continue
            current_rank = severity_order.get(str(current.get("severity") or "").lower(), 9)
            new_rank = severity_order.get(str(item.get("severity") or "").lower(), 9)
            if new_rank < current_rank:
                deduped[key] = dict(item)
                continue
            current_code = str(current.get("code") or "")
            new_code = str(item.get("code") or "")
            if new_code and new_code not in current_code.split(","):
                current["code"] = ",".join([value for value in [current_code, new_code] if value])
            if len(str(item.get("message") or "")) > len(str(current.get("message") or "")):
                current["message"] = item.get("message")
        return list(deduped.values())

    def _normalize_rel_path(self, value: str) -> str:
        return str(value or "").replace("\\", "/").strip()

    def _verify_alias_exists(self, alias: str, project_name: str, project_dir: str) -> Tuple[bool, Dict]:
        """Checks whether @/ alias is resolvable in sanctuary/main with project-aware fallbacks."""
        if not alias.startswith("@/"):
            return True, {"scope": "non_alias", "reason": "Non-@/ import."}

        rel_candidates = self._alias_rel_candidates(alias)
        scope_roots = self._scope_roots(project_name, project_dir)
        relocation_hint = self._find_relocation_hint(scope_roots, rel_candidates)

        # 1) Sanctuary-local resolution is strongest truth.
        for scope, root in scope_roots:
            if scope != "sanctuary":
                continue
            resolved_path = self._resolve_alias_under_root(root, rel_candidates)
            if resolved_path:
                return True, {
                    "scope": scope,
                    "detail": str(resolved_path),
                    "reason": "Alias resolved in sanctuary snapshot.",
                    "classification": "sanctuary_resolved",
                    "severity": "none",
                }

        # 2) MAIN host contracts are valid import boundaries.
        for scope, root in scope_roots:
            if scope != "main":
                continue
            resolved_path = self._resolve_alias_under_root(root, rel_candidates)
            if resolved_path:
                return True, {
                    "scope": scope,
                    "detail": str(resolved_path),
                    "reason": "Alias resolved in MAIN host boundary.",
                    "classification": "main_boundary_resolved",
                    "severity": "none",
                }

        # 3) Exists in variation source but not sanctuary => extraction/drift warning.
        for scope, root in scope_roots:
            if scope != "variation_source":
                continue
            resolved_path = self._resolve_alias_under_root(root, rel_candidates)
            if resolved_path:
                return False, {
                    "scope": scope,
                    "detail": str(resolved_path),
                    "reason": "Alias exists in variation source but missing in sanctuary snapshot.",
                    "classification": "sanctuary_snapshot_gap",
                    "severity": "high",
                }

        if alias.startswith("@/src/"):
            if relocation_hint:
                return False, {
                    "scope": relocation_hint["scope"],
                    "detail": relocation_hint["detail"],
                    "reason": "Legacy @/src alias appears to point at a module that was relocated.",
                    "classification": "relocated_alias_candidate",
                    "severity": "medium",
                }
            return False, {
                "scope": "unresolved",
                "detail": "",
                "reason": "Legacy @/src alias unresolved in sanctuary, variation source, and MAIN.",
                "classification": "legacy_src_alias_mismatch",
                "severity": "medium",
            }

        if relocation_hint:
            return False, {
                "scope": relocation_hint["scope"],
                "detail": relocation_hint["detail"],
                "reason": "Alias target was not found at the requested path, but a likely relocated module exists elsewhere.",
                "classification": "relocated_alias_candidate",
                "severity": "medium",
            }

        return False, {
            "scope": "unresolved",
            "detail": "",
            "reason": "Alias unresolved in sanctuary, variation source, and MAIN.",
            "classification": "host_missing_alias",
            "severity": "medium",
        }

    def _alias_rel_candidates(self, alias: str) -> List[str]:
        if not alias.startswith("@/"):
            return []
        raw = alias.replace("@/", "", 1).lstrip("/")
        candidates = [raw]
        if raw.startswith("src/"):
            candidates.append(raw[4:])
        else:
            candidates.append(f"src/{raw}")
        return list(dict.fromkeys([c for c in candidates if c]))

    def _scope_roots(self, project_name: str, project_dir: str) -> List[Tuple[str, Path]]:
        roots: List[Tuple[str, Path]] = []
        project_path = Path(project_dir).resolve()
        roots.extend(self._register_scope_roots("sanctuary", project_path))

        variations = DYNAMIC_CONFIG.get("variations", {}) or {}
        project_rel = normalize_path(variations.get(project_name))
        if project_rel and project_rel != ".":
            try:
                source_root = self._source_root_for_project(project_name)
            except ValueError:
                source_root = None
            if source_root is not None:
                roots.extend(self._register_scope_roots("variation_source", source_root))

        main_root = Path(MAIN_PROJECT_ROOT).resolve()
        if is_target_path_contained(ROOT, main_root):
            roots.extend(self._register_scope_roots("main", main_root))

        # Stable ordering and de-duplication
        seen = set()
        ordered: List[Tuple[str, Path]] = []
        for scope, root in roots:
            key = (scope, str(root))
            if key in seen:
                continue
            seen.add(key)
            ordered.append((scope, root))
        return ordered

    def _register_scope_roots(self, scope: str, base: Path) -> List[Tuple[str, Path]]:
        roots = [(scope, base)]
        src_root = base / "src"
        if src_root != base:
            roots.append((scope, src_root))
        return roots

    def _find_relocation_hint(self, scope_roots: List[Tuple[str, Path]], rel_candidates: List[str]) -> Dict | None:
        seen = set()
        for scope, root in scope_roots:
            root_key = (scope, str(root))
            if root_key in seen or not root.exists():
                continue
            seen.add(root_key)
            for rel in rel_candidates:
                hint = self._search_by_terminal_segments(root, rel)
                if hint:
                    return {
                        "scope": scope,
                        "detail": str(hint),
                    }
        return None

    def _search_by_terminal_segments(self, root: Path, rel_path: str) -> Path | None:
        normalized = str(rel_path or "").replace("\\", "/").strip("/")
        if not normalized:
            return None
        segments = [segment for segment in normalized.split("/") if segment]
        tail_patterns: list[str] = []
        if len(segments) >= 2:
            tail_patterns.append("/".join(segments[-2:]))
        tail_patterns.append(segments[-1])
        source_files = _iter_files_under(root, {".ts", ".tsx", ".js", ".jsx", ".d.ts"})
        candidates: List[Path] = []
        for path in source_files:
            rel = path.relative_to(root).as_posix()
            rel_no_suffix = rel[: -len(path.suffix)] if path.suffix else rel
            for tail in tail_patterns:
                if (
                    rel_no_suffix.endswith(tail)
                    or rel.endswith(f"{tail}/index.ts")
                    or rel.endswith(f"{tail}/index.tsx")
                    or rel.endswith(f"{tail}/index.js")
                    or rel.endswith(f"{tail}/index.jsx")
                    or rel.endswith(f"{tail}/index.d.ts")
                ):
                    candidates.append(path)
                    break
            if candidates and len(segments) >= 2 and rel_no_suffix.endswith(tail_patterns[0]):
                break
        if not candidates:
            return None
        candidates = sorted({candidate.resolve() for candidate in candidates}, key=lambda item: (len(item.parts), str(item)))
        return candidates[0]

    def _resolve_alias_under_root(self, root: Path, rel_candidates: List[str]) -> Path | None:
        extensions = [".ts", ".tsx", ".js", ".jsx", ".d.ts"]
        index_extensions = ["index.ts", "index.tsx", "index.js", "index.jsx", "index.d.ts"]
        for rel in rel_candidates:
            candidate = root / rel
            if is_target_path_contained(root, candidate) and candidate.exists():
                return candidate
            for ext in extensions:
                with_ext = Path(f"{candidate}{ext}")
                if is_target_path_contained(root, with_ext) and with_ext.exists():
                    return with_ext
            for index_name in index_extensions:
                with_index = candidate / index_name
                if is_target_path_contained(root, with_index) and with_index.exists():
                    return with_index
        return None

    def _summarize_errors(self, errors: List[Dict]) -> Dict:
        summary = {
            "total": len(errors),
            "deep_tsc_errors": 0,
            "syntax_errors": 0,
            "alias_errors": 0,
            "resolution_errors": 0,
            "reasons": {},
            "scopes": {},
            "classifications": {},
            "severities": {},
        }
        for err in errors:
            reason = str(err.get("reason", "unknown"))
            scope = str(err.get("resolution_scope", "n/a"))
            classification = str(err.get("classification", "unknown"))
            severity = str(err.get("severity", "unknown"))
            if classification in SYNTAX_ERROR_CLASSES:
                summary["syntax_errors"] += 1
            elif classification in RESOLUTION_ERROR_CLASSES:
                summary["resolution_errors"] += 1
            if classification in TSC_ERROR_CLASSES or reason == "Deep TypeScript Validation Error":
                summary["deep_tsc_errors"] += 1
            elif classification in RESOLUTION_ERROR_CLASSES:
                summary["alias_errors"] += 1
            summary["reasons"][reason] = summary["reasons"].get(reason, 0) + 1
            summary["scopes"][scope] = summary["scopes"].get(scope, 0) + 1
            summary["classifications"][classification] = summary["classifications"].get(classification, 0) + 1
            summary["severities"][severity] = summary["severities"].get(severity, 0) + 1
        return summary

    def _derive_status(self, errors: List[Dict], summary: Dict) -> str:
        if not errors:
            return "PASS"
        classifications = set((summary.get("classifications") or {}).keys())
        if int(summary.get("deep_tsc_errors", 0) or 0) > 0:
            return "FAIL"
        if classifications.intersection({"malformed_jsx_token", "deep_tsc_parser_error"}):
            return "FAIL"
        if classifications and classifications.issubset({"sanctuary_snapshot_gap"}):
            return "REHYDRATE_REQUIRED"
        return "WARN"

    def _save_report(self, project_name: str, report: Dict):
        from tools.core.config import REPORTS_DIR
        report_path = REPORTS_DIR / f"oracle_{project_name.lower()}.json"
        save_json_atomic(report_path, report)
        logger.info(f"[ORACLE] Report generated: {report_path}")

def run_validation_oracle(project_name: str):
    from tools.core.config import ROOT
    oracle = ValidationOracle(str(ROOT))
    return oracle.validate_project(project_name)
