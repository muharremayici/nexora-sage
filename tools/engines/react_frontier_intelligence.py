from __future__ import annotations

import json
import os
import re
import time

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, CONFIG_FILE, DYNAMIC_CONFIG, RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.engine_progress import EngineProgress
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.logger import logger
from tools.core.react_evidence import atlas_evidence_kinds, attach_react_evidence_contract, first_pattern_line
from tools.core.report_surface_limits import report_surface_limit
from tools.core.source_snapshot_reader import load_source_text
from tools.core.subprocess_telemetry import run_observed_subprocess
from tools.core.runtime_project_scope import project_runtime_atlas


REACT_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")
TYPE_ANY_RE = re.compile(r"(?::\s*any\b|as\s+any\b|<any>|Array<any>|Record<string,\s*any>)")
TS_IGNORE_RE = re.compile(r"@ts-(?:ignore|expect-error)")
PROP_TYPE_RE = re.compile(r"\b(?:type|interface)\s+([A-Z][A-Za-z0-9_]*(?:Props|Input|Config|State))\b")
GENERIC_COMPONENT_RE = re.compile(r"\bfunction\s+[A-Z][A-Za-z0-9_]*\s*<[^>]+>\s*\(")
DANGEROUS_HTML_RE = re.compile(r"dangerouslySetInnerHTML\s*=\s*{{\s*__html\s*:")
HTML_INJECTION_RE = re.compile(r"\b(?:innerHTML|outerHTML|insertAdjacentHTML)\s*=")
CLIENT_SECRET_RE = re.compile(r"\b(?:localStorage|sessionStorage)\.(?:setItem|getItem)\s*\([^)]*(?:token|secret|apiKey|password|jwt)", re.IGNORECASE)
SECRET_ENV_RE = re.compile(r"\b(?:VITE_|NEXT_PUBLIC_)[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD)", re.IGNORECASE)
CLIENT_GUARD_RE = re.compile(r"\b(?:useSession|localStorage|sessionStorage|window\.location)\b")
SERVER_GUARD_RE = re.compile(r"\b(?:auth\(|getServerSession|requireAuth|currentUser|cookies\(|headers\()\b")
CLASSNAME_RE = re.compile(r"className\s*=\s*(?:\"([^\"]+)\"|'([^']+)')")
COMPONENT_NAME_RE = re.compile(r"\b(?:export\s+)?(?:function|const)\s+([A-Z][A-Za-z0-9_]*)\b")
ROUTE_FILE_RE = re.compile(r"(?:^|/)(?:app|pages)/.*(?:page|layout|route)\.(?:tsx|jsx|ts|js)$")
CLIENT_DIRECTIVE_RE = re.compile(r"^\s*['\"]use client['\"]", re.MULTILINE)
HYDRATION_NONDETERMINISM_RE = re.compile(r"\b(?:Date\.now|Math\.random|crypto\.randomUUID|performance\.now|new\s+Date\s*\()", re.MULTILINE)
BROWSER_GLOBAL_RE = re.compile(r"\b(?:window|document|navigator|localStorage|sessionStorage)\.", re.MULTILINE)
HYDRATION_GUARD_RE = re.compile(
    r"\b(?:useEffect|useLayoutEffect|typeof\s+window|suppressHydrationWarning|dynamic\s*\([^)]*ssr\s*:\s*false)",
    re.DOTALL,
)
PROP_BLOCK_RE = re.compile(
    r"\b(?:type|interface)\s+([A-Z][A-Za-z0-9_]*Props)\b\s*(?:=\s*)?{(?P<body>[^}]+)}",
    re.DOTALL,
)
NON_SERIALIZABLE_PROP_RE = re.compile(
    r"\b(?:Date|Map|Set|WeakMap|WeakSet|Promise|RegExp|URL|File|Blob|ReadableStream|AbortController)\s*(?:<|\b)|"
    r"\b(?:on[A-Z][A-Za-z0-9_]*|render[A-Z]?[A-Za-z0-9_]*)\??\s*:\s*(?:\([^)]*\)\s*=>|Function\b)|"
    r"\b[A-Za-z_$][\w$]*\??\s*:\s*new\s*\(",
    re.MULTILINE,
)
POLICY_PATH = CONFIG_DIR / "react_frontier_policy.json"
_POLICY_CACHE: dict[str, Any] | None = None
TS_DIAGNOSTICS_PATH = RAW_DIR / "ts_diagnostics.json"
TS_COLLECTOR = CODE_MAPS_DIR / "tools" / "engines" / "ts_diagnostics_collector.cjs"


def _policy_string_list(section: dict[str, Any], key: str) -> list[str]:
    values = section.get(key, [])
    if not isinstance(values, list):
        return []
    return [str(item).replace("\\", "/").strip() for item in values if str(item or "").strip()]


def _load_frontier_policy(force: bool = False) -> dict[str, Any]:
    global _POLICY_CACHE
    if _POLICY_CACHE is not None and not force:
        return _POLICY_CACHE

    configured = load_json_object_strict(POLICY_PATH, label="React frontier policy")
    artifacts = configured.get("evidence_artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
    gates = configured.get("evidence_gates", {})
    if not isinstance(gates, dict):
        gates = {}

    _POLICY_CACHE = {
        "meta": configured.get("meta", {"kind": "react_frontier_policy", "version": "default"}),
        "policy_source": str(POLICY_PATH),
        "evidence_artifacts": {
            "bundle_stats_names": _policy_string_list(artifacts, "bundle_stats_names"),
            "react_profiler_names": _policy_string_list(artifacts, "react_profiler_names"),
            "recommended_locations": _policy_string_list(artifacts, "recommended_locations"),
            "json_scan_keys": _policy_string_list(artifacts, "json_scan_keys"),
        },
        "evidence_gates": {
            "runtime_or_browser_dimensions": set(_policy_string_list(gates, "runtime_or_browser_dimensions")),
            "hydration_dimensions": set(_policy_string_list(gates, "hydration_dimensions")),
            "runtime_gate": str(gates.get("runtime_gate") or "browser_or_runtime_smoke"),
            "hydration_gate": str(gates.get("hydration_gate") or "ssr_hydration_smoke"),
        },
    }
    return _POLICY_CACHE


def _project_root(project: str) -> Path:
    rel = (DYNAMIC_CONFIG.get("variations") or {}).get(project, "")
    return (ROOT / rel).resolve()


def _read_project_file(project: str, rel_path: str) -> str:
    path = (_project_root(project) / rel_path).resolve()
    return load_source_text(
        project,
        rel_path,
        fallback_path=path,
        component="react_frontier_intelligence",
    ) or ""


def _is_source(rel_path: str) -> bool:
    return rel_path.replace("\\", "/").endswith(REACT_EXTENSIONS)


def _tier(score: int) -> str:
    if score >= 8:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


def _confidence(evidence_kinds: set[str], score: int) -> str:
    if "runtime_profile" in evidence_kinds or "bundle_stats" in evidence_kinds:
        return "confirmed" if score >= 8 else "likely"
    if "typescript_compiler" in evidence_kinds:
        return "likely" if score >= 6 else "probable"
    if len(evidence_kinds) >= 2 and score >= 6:
        return "likely"
    if score >= 4:
        return "probable"
    return "needs_runtime_proof"


def _finding(
    project: str,
    file: str,
    dimension: str,
    risk: str,
    evidence: str,
    score: int,
    action: str,
    evidence_kinds: set[str] | None = None,
    line: int = 1,
    evidence_scope: str | None = None,
) -> dict[str, Any]:
    kinds = evidence_kinds or {"static"}
    item = {
        "project": project,
        "file": file.replace("\\", "/"),
        "dimension": dimension,
        "risk": risk,
        "risk_tier": _tier(score),
        "confidence": _confidence(kinds, score),
        "score": score,
        "line": line,
        "evidence_kinds": sorted(kinds),
        "evidence": evidence,
        "recommended_action": action,
        **({"evidence_scope": evidence_scope} if evidence_scope else {}),
    }
    return attach_react_evidence_contract(
        item,
        evidence_kinds=kinds,
        line=line,
        current_confidence=item["confidence"],
    )


def _first_line(content: str, *patterns: re.Pattern[str]) -> int:
    for pattern in patterns:
        line = first_pattern_line(content, pattern, default=0)
        if line:
            return line
    return 1


def _class_tokens(content: str) -> list[str]:
    tokens: list[str] = []
    for match in CLASSNAME_RE.finditer(content):
        tokens.extend((match.group(1) or match.group(2) or "").split())
    return tokens


def _component_names(content: str) -> list[str]:
    return sorted(set(COMPONENT_NAME_RE.findall(content)))


def analyze_frontier_file(project: str, rel_path: str, content: str, atlas_file: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not _is_source(rel_path):
        return None
    normalized = rel_path.replace("\\", "/")
    findings: list[dict[str, Any]] = []
    
    # --- PHASE 1.2: AST-First, Regex-Complement ---
    atlas_symbols = atlas_file.get("symbols", []) if atlas_file else []
    
    # 1. AST vs Regex: Components
    atlas_components = [
        s["name"] for s in atlas_symbols 
        if "Feature:UIComponent" in s.get("features", []) or s.get("name", "").endswith("Provider")
    ]
    regex_components = _component_names(content)
    component_names = sorted(set(atlas_components + regex_components))
    components_confidence = "confirmed" if atlas_components else "probable"

    # 2. AST vs Regex: Prop Contracts
    atlas_prop_contracts = [
        s["name"] for s in atlas_symbols
        if s.get("type") in ("Interface", "TypeDefinition") and s.get("name", "").endswith(("Props", "Input", "Config", "State"))
    ]
    regex_prop_contracts = PROP_TYPE_RE.findall(content)
    prop_contracts = sorted(set(atlas_prop_contracts + regex_prop_contracts))
    prop_confidence = "confirmed" if atlas_prop_contracts else "probable"

    file_evidence_kinds = atlas_evidence_kinds(atlas_file)

    any_count = len(TYPE_ANY_RE.findall(content))
    ts_escape_count = len(TS_IGNORE_RE.findall(content))
    
    if any_count or ts_escape_count:
        score = min(10, any_count * 2 + ts_escape_count * 3)
        item = _finding(
            project,
            normalized,
            "typescript_type_aware",
            "type_contract_escape_or_any_leak",
            f"any-like escapes={any_count}, ts suppressions={ts_escape_count}",
            score,
            "Use TypeScript compiler API validation or stricter local types before trusting this boundary.",
            {"static", "type_contract"},
            line=_first_line(content, TYPE_ANY_RE, TS_IGNORE_RE),
            evidence_scope="exact_type_escape_pattern",
        )
        item["confidence"] = "confirmed" if atlas_file else "probable"
        findings.append(item)

    if GENERIC_COMPONENT_RE.search(content) and not prop_contracts:
        item = _finding(
            project,
            normalized,
            "typescript_type_aware",
            "generic_component_without_named_prop_contract",
            "generic component found without nearby named Props/Input contract",
            4,
            "Name and export prop contracts so variation imports and AI refactors can preserve type intent.",
            {"static", "type_contract"},
            line=_first_line(content, GENERIC_COMPONENT_RE),
        )
        item["confidence"] = components_confidence
        findings.append(item)

    security_risks: list[str] = []
    score = 0
    if DANGEROUS_HTML_RE.search(content) or HTML_INJECTION_RE.search(content):
        security_risks.append("html injection sink")
        score += 6
    if CLIENT_SECRET_RE.search(content):
        security_risks.append("client storage token/secret access")
        score += 5
    if SECRET_ENV_RE.search(content):
        security_risks.append("public env name appears secret-like")
        score += 4
        
    atlas_features = set()
    if atlas_file:
        for s in atlas_symbols:
            if s.get("name") == "__file_meta__":
                atlas_features.update(s.get("features", []))
                break
                
    is_route = ROUTE_FILE_RE.search(normalized) or any("react_router" == s.get("contractKind") for s in atlas_symbols)
    has_client_guard = CLIENT_GUARD_RE.search(content) or "Feature:useSession" in atlas_features
    has_server_guard = SERVER_GUARD_RE.search(content) or "Feature:getServerSession" in atlas_features
    
    if is_route and has_client_guard and not has_server_guard:
        security_risks.append("route appears client-guarded without server guard")
        score += 5
        
    if security_risks:
        item = _finding(
            project,
            normalized,
            "react_security",
            "client_security_boundary_risk",
            "; ".join(security_risks),
            score,
            "Verify sanitization, server-side authorization and secret handling before shipping or importing this surface.",
            {"static", "security"},
            line=_first_line(content, DANGEROUS_HTML_RE, HTML_INJECTION_RE, CLIENT_SECRET_RE, SECRET_ENV_RE, CLIENT_GUARD_RE),
        )
        item["confidence"] = "confirmed" if atlas_file else "probable"
        findings.append(item)

    hydration_risks: list[str] = []
    hydration_score = 0
    nondeterministic = sorted(set(match.group(0).replace(" ", "") for match in HYDRATION_NONDETERMINISM_RE.finditer(content)))
    browser_globals = sorted(set(match.group(0).rstrip(".") for match in BROWSER_GLOBAL_RE.finditer(content)))
    guarded = bool(HYDRATION_GUARD_RE.search(content))
    if component_names and nondeterministic:
        hydration_risks.append(f"non-deterministic render inputs: {', '.join(nondeterministic[:5])}")
        hydration_score += 6
    if component_names and browser_globals and not guarded:
        hydration_risks.append(f"browser globals without visible hydration guard: {', '.join(browser_globals[:5])}")
        hydration_score += 4
    if hydration_risks:
        item = _finding(
            project,
            normalized,
            "hydration_determinism",
            "possible_ssr_client_hydration_mismatch",
            "; ".join(hydration_risks),
            min(10, hydration_score),
            "Move non-deterministic/browser-only reads into effect, server data, stable props, or an explicit no-SSR boundary.",
            {"static", "hydration_contract"} if not guarded else {"static", "hydration_contract", "guarded_context"},
            line=_first_line(content, HYDRATION_NONDETERMINISM_RE, BROWSER_GLOBAL_RE),
        )
        item["confidence"] = components_confidence
        findings.append(item)

    serialization_risks: list[str] = []
    if CLIENT_DIRECTIVE_RE.search(content):
        for match in PROP_BLOCK_RE.finditer(content):
            body = match.group("body")
            risky_tokens = sorted(set(token.group(0).strip() for token in NON_SERIALIZABLE_PROP_RE.finditer(body)))
            if risky_tokens:
                serialization_risks.append(f"{match.group(1)}: {', '.join(risky_tokens[:5])}")
    if serialization_risks:
        item = _finding(
            project,
            normalized,
            "rsc_serialization_contract",
            "client_component_props_may_not_be_rsc_serializable",
            "; ".join(serialization_risks[:4]),
            min(10, 4 + len(serialization_risks) * 2),
            "Convert client props to JSON-serializable DTOs and pass callbacks through client-owned composition instead of server boundaries.",
            {"static", "rsc_boundary", "type_contract"},
            line=_first_line(content, PROP_BLOCK_RE),
        )
        item["confidence"] = prop_confidence
        findings.append(item)

    tokens = _class_tokens(content)
    if component_names and len(tokens) >= 30:
        normalized_recipe = _visual_recipe(tokens)
        findings.append(
            _finding(
                project,
                normalized,
                "design_system_mining",
                "component_variant_extraction_candidate",
                f"{len(tokens)} utility classes; visual recipe={normalized_recipe}",
                min(8, 3 + len(tokens) // 30),
                "Mine this component into reusable variants/tokens if similar recipes recur across the workspace.",
                {"static", "design_system_mining"},
                line=_first_line(content, CLASSNAME_RE),
            )
        )

    if not findings:
        return None
    if file_evidence_kinds:
        findings = [
            attach_react_evidence_contract(
                dict(item),
                evidence_kinds=set(item.get("evidence_kinds", [])) | file_evidence_kinds,
                current_confidence=str(item.get("confidence") or ""),
            )
            for item in findings
        ]
    return {
        "project": project,
        "file": normalized,
        "components": component_names,
        "type_contracts": prop_contracts,
        "visual_recipe": _visual_recipe(tokens),
        "findings": findings,
    }


def _visual_recipe(tokens: list[str]) -> str:
    groups = Counter()
    for token in tokens:
        base = token.split(":", 1)[-1]
        prefix = base.split("-", 1)[0]
        if prefix:
            groups[prefix] += 1
    return ",".join(f"{key}:{value}" for key, value in sorted(groups.items())[:12])


def _discover_json_evidence(project: str, names: list[str]) -> list[dict[str, Any]]:
    root = _project_root(project)
    found: list[dict[str, Any]] = []
    policy = _load_frontier_policy()["evidence_artifacts"]
    locations = [""] + [str(item).strip("/\\") for item in policy.get("recommended_locations", [])]
    candidates = []
    for location in locations:
        for name in names:
            candidates.append(root / location / name if location else root / name)
    for path in candidates:
            if not path.exists() or not path.is_file():
                continue
            try:
                if path.stat().st_size > 8_000_000:
                    continue
            except OSError:
                continue
            payload = load_json_file(path, None)
            if payload is not None:
                found.append({"path": str(path.relative_to(root)).replace("\\", "/"), "payload": payload, "bytes": path.stat().st_size})
    return found[:8]


def _bundle_evidence(projects: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    names = _load_frontier_policy()["evidence_artifacts"]["bundle_stats_names"]
    for project in projects:
        entries = _discover_json_evidence(project, names)
        evidence.extend({"project": project, "path": entry["path"], "kind": "bundle_stats", "bytes": entry.get("bytes", 0)} for entry in entries)
        if not entries:
            continue
        for entry in entries:
            large_assets = _large_assets(entry["payload"])
            if large_assets:
                findings.append(
                    _finding(
                        project,
                        entry["path"],
                        "real_bundle_evidence",
                        "large_bundle_asset_or_chunk",
                        ", ".join(large_assets[:6]),
                        8,
                        "Correlate this asset with route/component ownership and split high-cost dependencies.",
                        {"bundle_stats"},
                    )
                )
    return evidence, findings


def _large_assets(payload: Any) -> list[str]:
    rows: list[tuple[str, float]] = []
    scan_keys = tuple(_load_frontier_policy()["evidence_artifacts"]["json_scan_keys"])

    def visit(node: Any, name_hint: str = "") -> None:
        if isinstance(node, dict):
            name = str(node.get("name") or node.get("file") or node.get("filename") or node.get("label") or name_hint)
            size = (
                node.get("size")
                or node.get("gzipSize")
                or node.get("parsedSize")
                or node.get("statSize")
                or node.get("renderedLength")
                or node.get("brotliSize")
            )
            if isinstance(size, (int, float)) and size >= 250_000:
                rows.append((name or "asset", float(size)))
            for key in scan_keys:
                value = node.get(key)
                if isinstance(value, list):
                    for item in value:
                        visit(item, name)
        elif isinstance(node, list):
            for item in node:
                visit(item, name_hint)

    visit(payload)
    return [f"{name}={int(size)}B" for name, size in sorted(rows, key=lambda item: -item[1])[:12]]


def _profiler_evidence(projects: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    names = _load_frontier_policy()["evidence_artifacts"]["react_profiler_names"]
    for project in projects:
        entries = _discover_json_evidence(project, names)
        evidence.extend({"project": project, "path": entry["path"], "kind": "react_profiler", "bytes": entry.get("bytes", 0)} for entry in entries)
        for entry in entries:
            hot = _hot_profiler_entries(entry["payload"])
            if hot:
                findings.append(
                    _finding(
                        project,
                        entry["path"],
                        "react_profiler_runtime",
                        "expensive_runtime_render_component",
                        ", ".join(hot[:6]),
                        9,
                        "Use profiler-confirmed render cost to prioritize memoization, state split or virtualization.",
                        {"runtime_profile"},
                    )
                )
    return evidence, findings


def _hot_profiler_entries(payload: Any) -> list[str]:
    rows: list[tuple[str, float]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            name = str(node.get("componentName") or node.get("name") or node.get("displayName") or "")
            duration = (
                node.get("actualDuration")
                or node.get("duration")
                or node.get("selfDuration")
                or node.get("treeBaseDuration")
                or node.get("maxActualDuration")
            )
            if name and isinstance(duration, (int, float)) and duration >= 16:
                rows.append((name, float(duration)))
            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    return [f"{name}={duration:.1f}ms" for name, duration in sorted(rows, key=lambda item: -item[1])[:12]]


def _collect_ts_diagnostics(timeout_seconds: int = 90, projects: list[str] | None = None) -> dict[str, Any]:
    if not TS_COLLECTOR.exists():
        return {"status": "COLLECTOR_MISSING", "path": str(TS_COLLECTOR), "summary": {"total_diagnostics": 0, "projects": 0}}
    cmd = [
        "node",
        str(TS_COLLECTOR),
        "--config",
        str(CONFIG_FILE),
        "--out",
        str(TS_DIAGNOSTICS_PATH),
        "--maxDiagnostics",
        "250",
        "--maxFiles",
        "260",
        "--semantic",
        "false",
    ]
    if projects:
        cmd.extend(["--projects", ",".join(projects)])
    try:
        safe_env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP"}}
        result, duration = run_observed_subprocess(
            cmd,
            cwd=CODE_MAPS_DIR,
            label="react_frontier_ts_diagnostics",
            timeout=timeout_seconds,
            env=safe_env,
            log=logger.info,
        )
        logger.info(
            "React Frontier TS diagnostics collector finished rc=%s duration_seconds=%.3f timeout_seconds=%s",
            result.returncode,
            duration,
            timeout_seconds,
        )
    except FileNotFoundError:
        return {"status": "NODE_MISSING", "summary": {"total_diagnostics": 0, "projects": 0}}
    if result.returncode == 124:
        return {"status": "TIMEOUT", "timeout_seconds": timeout_seconds, "summary": {"total_diagnostics": 0, "projects": 0}}

    payload = load_json_file(TS_DIAGNOSTICS_PATH, {}, bypass_proxy=True)
    if not isinstance(payload, dict) or not payload:
        return {
            "status": "NO_OUTPUT",
            "exit_code": result.returncode,
            "stdout": result.stdout[-500:],
            "stderr": result.stderr[-500:],
            "summary": {"total_diagnostics": 0, "projects": 0},
        }
    payload["collector_status"] = "OK" if result.returncode == 0 else "NONZERO_EXIT"
    payload["collector_exit_code"] = result.returncode
    save_json_atomic(TS_DIAGNOSTICS_PATH, payload)
    return payload


def _ts_diagnostic_findings(ts_payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    projects = ts_payload.get("projects", {}) if isinstance(ts_payload, dict) else {}
    if not isinstance(projects, dict):
        return findings
    critical_codes = {"TS2322", "TS2339", "TS2345", "TS2769", "TS2786", "TS2741", "TS2740", "TS7006", "TS7031"}
    for project, pdata in projects.items():
        if not isinstance(pdata, dict):
            continue
        diagnostics = pdata.get("diagnostics", []) if isinstance(pdata.get("diagnostics"), list) else []
        by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for diag in diagnostics:
            if isinstance(diag, dict) and diag.get("file"):
                by_file[str(diag["file"])].append(diag)
        for rel_path, rows in by_file.items():
            codes = Counter(str(row.get("code")) for row in rows)
            critical = sum(count for code, count in codes.items() if code in critical_codes)
            score = min(10, 3 + len(rows) + critical * 2)
            first_line = min(
                int(row.get("line") or 1)
                for row in rows
                if isinstance(row, dict)
            )
            findings.append(
                _finding(
                    str(project),
                    rel_path,
                    "typescript_compiler_diagnostics",
                    "compiler_reported_type_contract_failure",
                    ", ".join(f"{code}={count}" for code, count in codes.most_common(6)),
                    score,
                    "Use compiler diagnostics as primary evidence; repair type contracts before relying on static heuristic confidence.",
                    {"typescript_compiler"},
                    line=first_line,
                )
            )
    return findings


def _ts_diagnostics_by_file(ts_payload: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    projects = ts_payload.get("projects", {}) if isinstance(ts_payload, dict) else {}
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    if not isinstance(projects, dict):
        return index
    for project, pdata in projects.items():
        if not isinstance(pdata, dict):
            continue
        diagnostics = pdata.get("diagnostics", []) if isinstance(pdata.get("diagnostics"), list) else []
        for diag in diagnostics:
            if isinstance(diag, dict) and diag.get("file"):
                index[(str(project), str(diag["file"]).replace("\\", "/"))].append(diag)
    return index


def _enrich_findings_with_ts_diagnostics(
    findings: list[dict[str, Any]],
    ts_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    index = _ts_diagnostics_by_file(ts_payload)
    enriched: list[dict[str, Any]] = []
    enriched_count = 0
    dimensions = {
        "typescript_type_aware",
        "rsc_serialization_contract",
        "react_security",
        "hydration_determinism",
    }
    for item in findings:
        key = (str(item.get("project") or ""), str(item.get("file") or "").replace("\\", "/"))
        rows = index.get(key, [])
        if not rows or str(item.get("dimension") or "") not in dimensions:
            enriched.append(item)
            continue
        next_item = dict(item)
        diagnostic_rows = [
            {
                "code": row.get("code"),
                "line": row.get("line"),
                "character": row.get("character"),
                "message": row.get("message"),
            }
            for row in rows[:6]
        ]
        kinds = set(next_item.get("evidence_kinds") or [])
        kinds.add("typescript_compiler")
        next_item["typescript_diagnostics"] = diagnostic_rows
        next_item["typescript_diagnostic_codes"] = sorted({str(row.get("code")) for row in rows if row.get("code")})
        next_item["evidence"] = (
            f"{next_item.get('evidence', '')}; TypeScript diagnostics: "
            + ", ".join(next_item["typescript_diagnostic_codes"][:6])
        ).strip("; ")
        next_item["score"] = min(10, int(next_item.get("score", 0) or 0) + min(3, len(rows)))
        next_item = attach_react_evidence_contract(
            next_item,
            evidence_kinds=kinds,
            current_confidence=str(next_item.get("confidence") or ""),
        )
        enriched.append(next_item)
        enriched_count += 1
    return enriched, enriched_count


def _refactor_plan(findings: list[dict[str, Any]]) -> dict[str, Any]:
    gate_policy = _load_frontier_policy()["evidence_gates"]
    priority = sorted(findings, key=lambda item: (-int(item.get("score", 0)), item.get("project", ""), item.get("file", "")))[:40]
    tasks = []
    for item in priority:
        dimension = item.get("dimension")
        gates = ["typecheck", "unit_or_contract_test"]
        if dimension in gate_policy["runtime_or_browser_dimensions"]:
            gates.append(gate_policy["runtime_gate"])
        if dimension in gate_policy["hydration_dimensions"] or dimension == "hydration_determinism":
            gates.extend([gate_policy["runtime_gate"], gate_policy["hydration_gate"]])
        tasks.append(
            {
                "project": item.get("project"),
                "file": item.get("file"),
                "dimension": dimension,
                "risk": item.get("risk"),
                "confidence": item.get("confidence"),
                "patch_strategy": _patch_strategy(str(dimension)),
                "verification_gates": gates,
            }
        )
    return {"tasks": tasks, "total_tasks": len(tasks)}


def _patch_strategy(dimension: str) -> str:
    return {
        "typescript_type_aware": "replace type escapes with explicit exported contracts and compiler-checked generics",
        "typescript_compiler_diagnostics": "repair compiler-reported type contract failures before heuristic refactors",
        "react_security": "move trust boundary to server, sanitize HTML, remove client secret persistence",
        "hydration_determinism": "isolate non-deterministic/browser-only reads behind effects, stable props, or no-SSR boundary",
        "rsc_serialization_contract": "replace non-serializable client props with DTOs, ids, ISO strings, or client-owned callbacks",
        "design_system_mining": "extract repeated utility recipe into variant/token backed component",
        "real_bundle_evidence": "split heavy chunks with dynamic import or route-level lazy boundary",
        "react_profiler_runtime": "profile-confirmed render split, memo boundary, or virtualization",
    }.get(dimension, "make the smallest safe refactor and rerun Codemaps gates")


def _evidence_readiness(
    projects: list[str],
    bundle_evidence: list[dict[str, Any]],
    profiler_evidence: list[dict[str, Any]],
    ts_diagnostics: dict[str, Any],
) -> dict[str, Any]:
    bundle_by_project: dict[str, list[str]] = defaultdict(list)
    profiler_by_project: dict[str, list[str]] = defaultdict(list)
    for item in bundle_evidence:
        bundle_by_project[str(item.get("project"))].append(str(item.get("path")))
    for item in profiler_evidence:
        profiler_by_project[str(item.get("project"))].append(str(item.get("path")))

    rows = []
    for project in projects:
        has_bundle = bool(bundle_by_project.get(project))
        has_profiler = bool(profiler_by_project.get(project))
        status = "PASS" if has_bundle and has_profiler else ("PARTIAL" if has_bundle or has_profiler else "NEEDS_EVIDENCE")
        rows.append(
            {
                "project": project,
                "status": status,
                "bundle_stats_found": bundle_by_project.get(project, []),
                "react_profiler_found": profiler_by_project.get(project, []),
                "missing": [
                    label
                    for label, found in (
                        ("bundle_stats", has_bundle),
                        ("react_profiler", has_profiler),
                    )
                    if not found
                ],
            }
        )

    status_counts = Counter(row["status"] for row in rows)
    ts_summary = ts_diagnostics.get("summary", {}) if isinstance(ts_diagnostics, dict) else {}
    collector_status = str(ts_diagnostics.get("collector_status") or ts_diagnostics.get("status") or "UNKNOWN")
    artifact_policy = _load_frontier_policy()["evidence_artifacts"]
    return {
        "meta": {"kind": "react_frontier_evidence_readiness", "version": "v1"},
        "summary": {
            "projects": len(projects),
            "ready_projects": status_counts.get("PASS", 0),
            "partial_projects": status_counts.get("PARTIAL", 0),
            "needs_evidence_projects": status_counts.get("NEEDS_EVIDENCE", 0),
            "bundle_evidence_files": len(bundle_evidence),
            "profiler_evidence_files": len(profiler_evidence),
            "typescript_collector_status": collector_status,
            "typescript_diagnostics_total": int((ts_summary or {}).get("total_diagnostics", 0) or 0),
        },
        "expected_artifacts": {
            "bundle_stats_names": artifact_policy["bundle_stats_names"],
            "react_profiler_names": artifact_policy["react_profiler_names"],
            "search_scope": "project tree excluding node_modules and .git; JSON files over 8MB are skipped",
            "recommended_locations": artifact_policy["recommended_locations"],
        },
        "how_to_generate": [
            "Export a production bundle analyzer JSON as one of the expected bundle stats filenames.",
            "Export React DevTools Profiler JSON as one of the expected profiler filenames.",
            "Rerun Codemaps full/force so confirmed bundle and render-cost evidence can calibrate static React findings.",
        ],
        "projects": rows,
    }


def run_react_frontier_intelligence() -> dict[str, Any]:
    logger.info("Analyzing React frontier intelligence...")
    progress = EngineProgress("react_frontier_intelligence")
    progress.start()
    started = time.perf_counter()
    canonical_atlas = load_atlas_data()
    atlas, execution_scope = project_runtime_atlas(canonical_atlas if isinstance(canonical_atlas, dict) else {})
    atlas_loaded_at = time.perf_counter()
    files: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    projects = sorted(atlas.keys()) if isinstance(atlas, dict) else []
    project_profile: dict[str, dict[str, Any]] = {}

    if isinstance(atlas, dict):
        for project, pdata in atlas.items():
            project_started = time.perf_counter()
            files_before = len(files)
            findings_before = len(findings)
            project_files = (pdata or {}).get("files", {}) if isinstance(pdata, dict) else {}
            progress.phase("project_source_scan", total=len(project_files), current_project=project)
            for file_completed, (rel_path, fdata) in enumerate(project_files.items(), start=1):
                content = _read_project_file(project, str(rel_path))
                row = analyze_frontier_file(project, str(rel_path), content, fdata if isinstance(fdata, dict) else {})
                progress.advance(file_completed, current_project=project)
                if not row:
                    continue
                files.append({key: value for key, value in row.items() if key != "findings"})
                findings.extend(row["findings"])
            project_profile[str(project)] = {
                "candidate_files": len(project_files),
                "frontier_files": len(files) - files_before,
                "findings": len(findings) - findings_before,
                "seconds": round(time.perf_counter() - project_started, 3),
            }
            logger.info(
                "[REACT_FRONTIER_PROFILE] project=%s %s",
                project,
                project_profile[str(project)],
            )
            progress.checkpoint("project_complete", project=project, **project_profile[str(project)])

    evidence_started = time.perf_counter()
    progress.phase("evidence_import")
    bundle_evidence, bundle_findings = _bundle_evidence(projects)
    profiler_evidence, profiler_findings = _profiler_evidence(projects)
    ts_diagnostics = _collect_ts_diagnostics(projects=projects)
    evidence_finished = time.perf_counter()
    ts_findings = _ts_diagnostic_findings(ts_diagnostics)
    findings.extend(bundle_findings)
    findings.extend(profiler_findings)
    findings.extend(ts_findings)
    findings, ts_correlated_findings = _enrich_findings_with_ts_diagnostics(findings, ts_diagnostics)

    dimension_counts = Counter(str(item.get("dimension")) for item in findings)
    confidence_counts = Counter(str(item.get("confidence")) for item in findings)
    tier_counts = Counter(str(item.get("risk_tier")) for item in findings)

    sorted_findings = sorted(findings, key=lambda item: (-int(item.get("score", 0)), item.get("project", ""), item.get("file", "")))
    primary_limit = report_surface_limit("react_frontier_intelligence.primary_findings")
    artifact_policy = _load_frontier_policy()["evidence_artifacts"]
    summary = {
        "files_analyzed": len(files),
        "findings": len(findings),
        "dimension_counts": dict(dimension_counts),
        "confidence_counts": dict(confidence_counts),
        "risk_tiers": dict(tier_counts),
        "high_risk": tier_counts.get("high", 0),
        "medium_risk": tier_counts.get("medium", 0),
        "bundle_evidence_files": len(bundle_evidence),
        "profiler_evidence_files": len(profiler_evidence),
        "ts_diagnostics_total": (ts_diagnostics.get("summary", {}) or {}).get("total_diagnostics", 0),
        "ts_diagnostics_projects_with_errors": (ts_diagnostics.get("summary", {}) or {}).get("projects_with_errors", 0),
        "ts_correlated_findings": ts_correlated_findings,
        "primary_report_finding_limit": primary_limit,
        "truncated_in_primary_report": max(0, len(sorted_findings) - primary_limit),
        "full_artifact": "react_frontier_intelligence_full.json",
        "evidence_readiness_artifact": "react_frontier_evidence_readiness.json",
    }
    payload = {
        "meta": {
            "kind": "react_frontier_intelligence",
            "version": "v1",
            "execution_scope": execution_scope,
        },
        "summary": summary,
        "evidence_imports": {
            "bundle_stats": bundle_evidence,
            "react_profiler": profiler_evidence,
            "typescript_diagnostics": {
                "status": ts_diagnostics.get("collector_status") or ts_diagnostics.get("status") or "UNKNOWN",
                "summary": ts_diagnostics.get("summary", {}),
                "artifact": str(TS_DIAGNOSTICS_PATH),
            },
            "bundle_stats_expected_names": artifact_policy["bundle_stats_names"],
            "react_profiler_expected_names": artifact_policy["react_profiler_names"],
        },
        "files": files,
        "findings": sorted_findings[:primary_limit],
        "refactor_plan": _refactor_plan(findings),
    }
    full_payload = {
        **payload,
        "meta": {
            "kind": "react_frontier_intelligence_full",
            "version": "v1",
            "source": "react_frontier_intelligence",
            "execution_scope": execution_scope,
        },
        "summary": {
            **summary,
            "primary_report_finding_limit": len(sorted_findings),
            "truncated_in_primary_report": 0,
        },
        "findings": sorted_findings,
    }
    readiness = _evidence_readiness(projects, bundle_evidence, profiler_evidence, ts_diagnostics)
    save_json_atomic(RAW_DIR / "react_frontier_intelligence.json", payload)
    save_json_atomic(RAW_DIR / "react_frontier_intelligence_full.json", full_payload)
    save_json_atomic(RAW_DIR / "react_frontier_evidence_readiness.json", readiness)
    save_text_atomic(REPORTS_DIR / "react_frontier_intelligence.md", _render_markdown(payload))
    save_text_atomic(REPORTS_DIR / "react_frontier_evidence_readiness.md", _render_evidence_readiness_markdown(readiness))
    logger.info(
        "[REACT_FRONTIER_PROFILE] total=%s",
        {
            "atlas_load_seconds": round(atlas_loaded_at - started, 3),
            "source_scan_seconds": round(evidence_started - atlas_loaded_at, 3),
            "evidence_import_seconds": round(evidence_finished - evidence_started, 3),
            "total_seconds": round(time.perf_counter() - started, 3),
            "files_analyzed": len(files),
            "findings": len(findings),
            "projects": project_profile,
        },
    )
    progress.complete("PASS", projects=len(project_profile), files=len(files), findings=len(findings))
    return payload


def _render_evidence_readiness_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# React Frontier Evidence Readiness",
        "",
        f"- Projects: `{summary.get('projects', 0)}`",
        f"- Ready projects: `{summary.get('ready_projects', 0)}`",
        f"- Partial projects: `{summary.get('partial_projects', 0)}`",
        f"- Needs evidence: `{summary.get('needs_evidence_projects', 0)}`",
        f"- Bundle evidence files: `{summary.get('bundle_evidence_files', 0)}`",
        f"- React profiler evidence files: `{summary.get('profiler_evidence_files', 0)}`",
        f"- TypeScript collector: `{summary.get('typescript_collector_status', 'UNKNOWN')}`",
        "",
        "## Expected Artifact Names",
        "",
        f"- Bundle stats: `{', '.join((payload.get('expected_artifacts', {}) or {}).get('bundle_stats_names', []))}`",
        f"- React profiler: `{', '.join((payload.get('expected_artifacts', {}) or {}).get('react_profiler_names', []))}`",
        "",
        "## Projects",
        "",
        "| Project | Status | Missing | Bundle Stats | React Profiler |",
        "|---|---|---|---|---|",
    ]
    for row in payload.get("projects", []):
        lines.append(
            f"| `{row.get('project')}` | `{row.get('status')}` | `{', '.join(row.get('missing', [])) or '-'}` | "
            f"`{', '.join(row.get('bundle_stats_found', [])) or '-'}` | `{', '.join(row.get('react_profiler_found', [])) or '-'}` |"
        )
    lines.extend(["", "## How To Generate", ""])
    for item in payload.get("how_to_generate", []):
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# React Frontier Intelligence",
        "",
        f"- Files analyzed: `{summary.get('files_analyzed', 0)}`",
        f"- Findings: `{summary.get('findings', 0)}`",
        f"- High risk: `{summary.get('high_risk', 0)}`",
        f"- Medium risk: `{summary.get('medium_risk', 0)}`",
        f"- Bundle evidence files: `{summary.get('bundle_evidence_files', 0)}`",
        f"- React profiler evidence files: `{summary.get('profiler_evidence_files', 0)}`",
        f"- TypeScript diagnostics: `{summary.get('ts_diagnostics_total', 0)}`",
        f"- TypeScript projects with errors: `{summary.get('ts_diagnostics_projects_with_errors', 0)}`",
        "",
        "## Dimensions",
        "",
        "| Dimension | Count |",
        "|---|---:|",
    ]
    for dimension, count in Counter(summary.get("dimension_counts", {}) or {}).most_common():
        lines.append(f"| `{dimension}` | {count} |")
    lines.extend(["", "## Evidence Imports", ""])
    if not summary.get("bundle_evidence_files"):
        lines.append("- No bundle stats artifact found; real bundle cost remains static/fallback evidence.")
    if not summary.get("profiler_evidence_files"):
        lines.append("- No React profiler export found; runtime render cost remains static/fallback evidence.")
    ts_evidence = (payload.get("evidence_imports", {}) or {}).get("typescript_diagnostics", {})
    lines.append(
        f"- TypeScript diagnostics collector: `{ts_evidence.get('status', 'UNKNOWN')}` "
        f"total=`{(ts_evidence.get('summary', {}) or {}).get('total_diagnostics', 0)}`"
    )
    lines.extend(["", "## Top Findings", "", "| Project | File | Tier | Confidence | Dimension | Risk | Evidence | Action |", "|---|---|---|---|---|---|---|---|"])
    for item in payload.get("findings", [])[:100]:
        lines.append(
            f"| `{item.get('project')}` | `{item.get('file')}` | `{item.get('risk_tier')}` | `{item.get('confidence')}` | "
            f"`{item.get('dimension')}` | `{item.get('risk')}` | {item.get('evidence', '-')} | {item.get('recommended_action', '-')} |"
        )
    lines.extend(["", "## AI Refactor Patch Plan", "", "| Project | File | Strategy | Gates |", "|---|---|---|---|"])
    for item in (payload.get("refactor_plan", {}) or {}).get("tasks", [])[:40]:
        lines.append(
            f"| `{item.get('project')}` | `{item.get('file')}` | {item.get('patch_strategy')} | `{', '.join(item.get('verification_gates', []))}` |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    run_react_frontier_intelligence()
