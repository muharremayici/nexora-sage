from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, DYNAMIC_CONFIG, RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.json_io import load_json_object_strict
from tools.core.logger import logger
from tools.core.report_surface_limits import report_surface_limit
from tools.core.source_snapshot_reader import load_source_text


POLICY_PATH = CONFIG_DIR / "next_boundary_policy.json"
_POLICY_CACHE: dict[str, Any] | None = None


def _load_policy(force: bool = False) -> dict[str, Any]:
    global _POLICY_CACHE
    if _POLICY_CACHE is not None and not force:
        return _POLICY_CACHE
    _POLICY_CACHE = load_json_object_strict(POLICY_PATH, label="Next boundary policy")
    return _POLICY_CACHE


def _policy_pattern(name: str, fallback: str) -> re.Pattern[str]:
    configured = ((_load_policy().get("contract_patterns") or {}).get(name))
    pattern = str(configured or fallback)
    try:
        return re.compile(pattern, re.MULTILINE)
    except re.error:
        logger.warning("Invalid next boundary policy regex for %s; using generic fallback.", name)
        return re.compile(fallback, re.MULTILINE)


def _policy_list(section: str, key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    configured = ((_load_policy().get(section) or {}).get(key))
    if isinstance(configured, list):
        values = tuple(str(item).lower() for item in configured if str(item).strip())
        if values:
            return values
    return fallback


CLIENT_DIRECTIVE_RE = re.compile(r"^\s*['\"]use client['\"]\s*;?", re.MULTILINE)
SERVER_DIRECTIVE_RE = re.compile(r"^\s*['\"]use server['\"]\s*;?", re.MULTILINE)
METADATA_CONTRACT_RE = re.compile(
    r"^\s*export\s+(?:const|let|var)\s+metadata\s*="
    r"|^\s*export\s+(?:async\s+)?function\s+generateMetadata\s*\(",
    re.MULTILINE,
)
SERVER_ACTION_RE = re.compile(r"\b(?:async\s+)?function\s+\w+\s*\([^)]*\)\s*{[^{}]*['\"]use server['\"]", re.DOTALL)
FORM_ACTION_BINDING_RE = re.compile(
    r"<(?:form|button)\b[^>]*\b(?:action|formAction)\s*=\s*{(?!\s*[`'\"])([^}]+)}",
    re.IGNORECASE | re.DOTALL,
)
FORM_NAVIGATION_ACTION_RE = re.compile(
    r"<(?:Form|form)\b[^>]*\baction\s*=\s*(?:[`'\"]|{\s*[`'\"])",
    re.DOTALL,
)
ACTION_STATE_RE = re.compile(r"\b(?:useActionState|useFormState|useFormStatus|isPending|pending)\b")
ACTION_ERROR_RE = re.compile(
    r"\b(?:try\s*{|catch\s*\(|throw\s+new|return\s+{[^}]*(?:error|success\s*:\s*false|message)|toast|useOptimistic|aria-live)\b",
    re.DOTALL,
)
ACTION_CLIENT_ERROR_STATE_RE = re.compile(
    _policy_pattern(
        "action_client_error_state",
        r"\b(?:state|formState|actionState)\.(?:error|message|success|status)\b",
    ).pattern,
    re.MULTILINE,
)
ACTION_CLIENT_CONTRACT_RE = re.compile(
    _policy_pattern(
        "action_client_contract",
        r"\b[A-Za-z_$][A-Za-z0-9_$]*ActionClient\b[\s\S]{0,500}\.action\s*\(",
    ).pattern,
    re.MULTILINE,
)
FRAMEWORK_SERVER_FUNCTION_CONTRACT_RE = re.compile(
    _policy_pattern("framework_server_function_contract", r"\b[A-Za-z_$][A-Za-z0-9_$]*ServerFunction[A-Za-z0-9_$]*\b").pattern,
    re.MULTILINE,
)
FETCH_CACHE_RE = re.compile(r"\bfetch\s*\([^)]*(?:cache|next\s*:|revalidate)", re.DOTALL)
RESPONSE_CACHE_CONTROL_RE = re.compile(
    r"['\"]Cache-Control['\"]\s*:"
    r"|\.headers\.set\s*\(\s*['\"]Cache-Control['\"]",
    re.IGNORECASE,
)
CLIENT_RUNTIME_SURFACE_RE = re.compile(
    r"\b(?:useState|useEffect|useLayoutEffect|useMemo|useCallback|useRef|useRouter|useParams|useSearchParams|usePathname|useSession|useQuery|useMutation)\s*\("
    r"|(?:\bwindow\.|\bdocument\.|\blocalStorage\b|\bsessionStorage\b|\bCookies\.)",
    re.MULTILINE,
)
MUTATION_METHOD_RE = re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\s*\(", re.MULTILINE)
MUTATION_CALL_RE = re.compile(
    r"\b(?:create|update|delete|remove|save|insert|upsert|mutate)[A-Za-z0-9_]*\s*\(",
    re.MULTILINE,
)
READ_ONLY_ROUTE_HELPER_RE = re.compile(r"\bcreateFromSource\s*\(", re.MULTILINE)
REVALIDATION_RE = re.compile(r"\b(?:revalidatePath|revalidateTag|updateTag|router\.refresh|refresh\s*\()\b")
STATIC_CACHE_SIGNAL_RE = re.compile(
    r"\b(?:unstable_cache|cacheTag|cacheLife|next\s*:\s*{[^}]*tags)"
    r"|^\s*(?:export\s+const\s+)?revalidate\s*="
    r"|^\s*(?:export\s+const\s+)?fetchCache\s*=\s*['\"](?!force-no-store)[^'\"]+['\"]",
    re.DOTALL | re.MULTILINE,
)
CACHE_OR_STATIC_SEGMENT_RE = re.compile(
    r"^\s*(?:export\s+const\s+)?revalidate\s*="
    r"|^\s*(?:export\s+const\s+)?fetchCache\s*=\s*['\"](?!force-no-store)[^'\"]+['\"]"
    r"|^\s*(?:export\s+const\s+)?dynamic\s*=\s*['\"]force-static['\"]",
    re.DOTALL | re.MULTILINE,
)
DYNAMIC_NO_STATIC_CACHE_RE = re.compile(
    r"\bdynamic\s*=\s*['\"]force-dynamic['\"]|fetchCache\s*=\s*['\"]force-no-store['\"]",
    re.DOTALL,
)
EDGE_RUNTIME_RE = re.compile(r"\bexport\s+const\s+runtime\s*=\s*['\"]edge['\"]")
NODE_RUNTIME_RE = re.compile(r"\bexport\s+const\s+runtime\s*=\s*['\"]nodejs['\"]")
NODE_ONLY_IMPORT_RE = re.compile(
    r"\bimport\s+(?:[^;]+?\s+from\s+)?['\"](?:node:)?(?:fs|path|crypto|child_process|worker_threads|net|tls|dns|os)['\"]"
)
NODE_ONLY_API_RE = re.compile(r"\b(?:require\s*\(\s*['\"](?:node:)?(?:fs|path|child_process|worker_threads|net|tls|dns|os)['\"]|process\.cwd\s*\()", re.MULTILINE)
ENV_ACCESS_RE = re.compile(r"\bprocess\.env\.([A-Za-z_$][A-Za-z0-9_$]*)")
INPUT_SURFACE_RE = re.compile(
    r"\b(?:request\.(?:json|text|formData)\s*\(|req\.(?:json|text|formData)\s*\(|params\.|searchParams\.|request\.nextUrl\.searchParams|req\.nextUrl\.searchParams|new\s+URL\s*\([^)]*(?:request|req)\.url)",
    re.DOTALL,
)
ROUTE_CONTEXT_PARAMS_RE = re.compile(
    r"\b(?:context|ctx)\.params\b"
    r"|(?:^|[,(])\s*{\s*params\s*}"
    r"|\bparams\s*:\s*{\s*[A-Za-z_$][A-Za-z0-9_$]*",
    re.DOTALL | re.MULTILINE,
)
VALIDATION_CONTRACT_RE = re.compile(
    r"\b(?:safeParse|parseAsync|z\.object|zodResolver|schema\s*=|Schema\s*=|validate[A-ZA-Za-z0-9_]*\s*\(|assert[A-ZA-Za-z0-9_]*\s*\()|"
    r"\b[A-Za-z_$][A-Za-z0-9_$]*(?:Schema|schema)\.parse\s*\(|"
    r"\bschemas\s*:",
    re.MULTILINE,
)
MANUAL_INPUT_GUARD_RE = re.compile(
    r"\b(?:typeof\s+[A-Za-z_$][A-Za-z0-9_$.]*\s*[!=]={1,2}\s*['\"](?:string|number|boolean)['\"]|"
    r"[A-Za-z_$][A-Za-z0-9_$.]*\s+instanceof\s+[A-Z][A-Za-z0-9_]*|"
    r"Array\.isArray\s*\(|"
    r"if\s*\(\s*!+[A-Za-z_$][A-Za-z0-9_$.]*(?:\s*(?:\|\||&&)\s*!+[A-Za-z_$][A-Za-z0-9_$.]*)*|"
    r"[A-Za-z_$][A-Za-z0-9_$.]*\s*[!=]={1,2}\s*['\"][^'\"]+['\"]|"
    r"\.[A-Za-z0-9_]*(?:startsWith|endsWith|includes|match|test)\s*\(|"
    r"process\.env\.[A-Za-z_$][A-Za-z0-9_$]*\s*[!=]={1,2}\s*[A-Za-z_$][A-Za-z0-9_$.]*|"
    r"\bget[A-Z][A-Za-z0-9_]*OrThrow\s*\(|"
    r"(?:consume|verify|parse|sanitize|getSafe)[A-Z][A-Za-z0-9_]*(?:OAuth|Callback|State|Error|Input|Params|Query)[A-Za-z0-9_]*\s*\()",
    re.MULTILINE,
)
FRAMEWORK_ROUTE_HANDLER_CONTRACT_RE = re.compile(
    r"\b(?:NextAuth|Auth|createRouteHandler|createRouteHandlers|withV3ApiWrapper|oauthController\.token)\s*\(",
    re.MULTILINE,
)
ROUTE_GUARD_CONTRACT_RE = re.compile(
    r"\b(?:withAdmin|withAuth|withSession|withUser|withWorkspace|withCron|withV3ApiWrapper|verifyQstashSignature|auth\s*\(|getServerSession\s*\(|cors\s*\()",
    re.MULTILINE,
)
SIGNED_REQUEST_CONTRACT_RE = re.compile(
    r"\b(?:verifyQstashSignature)\s*\(",
    re.MULTILINE,
)
TRANSITIVE_HELPER_VALIDATION_RE = re.compile(
    r"\b(?:verify|validate|sanitize|getValidated|assert|parse)[A-Z][A-Za-z0-9_]*(?:Intent|Token|Signature|JWT|Jwt|Callback|State|Input|Params|Query|Url|URL)[A-Za-z0-9_]*\s*\("
    r"|getServerSession\s*\(",
    re.MULTILINE,
)
RELATIVE_NAMED_IMPORT_RE = re.compile(
    r"import\s*{\s*(?P<names>[^}]+)\s*}\s*from\s*['\"](?P<source>\.{1,2}/[^'\"]+)['\"]",
    re.MULTILINE,
)
SERIALIZATION_DATA_PROP_TOKEN_RE = re.compile(
    r"\b[A-Za-z_$][A-Za-z0-9_$]*\??\s*:\s*(?:Date|Map\s*<|Set\s*<|Promise\s*<|RegExp|URL|File|Blob|ReadableStream|AbortController)\b",
    re.MULTILINE,
)
SERIALIZATION_CALLBACK_PROP_TOKEN_RE = re.compile(
    r"\b(?:on[A-Z][A-Za-z0-9_]*|render[A-Z]?[A-Za-z0-9_]*)\??\s*:\s*(?:\([^)]*\)\s*=>|Function\b)",
    re.MULTILINE,
)
EXPORT_COMPONENT_PARAMS_RE = re.compile(
    r"\bexport\s+(?:default\s+)?function\s+[A-Z][A-Za-z0-9_]*\s*\((?P<params>[\s\S]{0,1800}?)\)\s*{",
    re.MULTILINE,
)
EXPORT_CONST_COMPONENT_PARAMS_RE = re.compile(
    r"\bexport\s+const\s+[A-Z][A-Za-z0-9_]*\s*=\s*\((?P<params>[\s\S]{0,1800}?)\)\s*=>\s*(?:\{|\()",
    re.MULTILINE,
)


def _project_root(project: str) -> Path:
    rel = (DYNAMIC_CONFIG.get("variations") or {}).get(project, "")
    return (ROOT / rel).resolve()


def _read_project_file(project: str, rel_path: str) -> str:
    path = (_project_root(project) / rel_path).resolve()
    return load_source_text(
        project,
        rel_path,
        fallback_path=path,
        component="next_boundary_analyzer",
    ) or ""


def _is_next_path(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/").lower()
    if _is_reference_or_non_next_surface(normalized):
        return False
    return "/app/" in f"/{normalized}" or "/pages/" in f"/{normalized}" or normalized.startswith(("app/", "pages/"))


def _is_reference_or_non_next_surface(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/").lower()
    wrapped = f"/{normalized}"
    reference_markers = _policy_list(
        "reference_surfaces",
        "path_markers",
        (),
    )
    test_suffixes = _policy_list(
        "reference_surfaces",
        "file_suffixes",
        (),
    )
    return any(marker in wrapped for marker in reference_markers) or normalized.endswith(test_suffixes)


def _is_next_fetch_cache_scope(rel_path: str, content: str, signals: list[str]) -> bool:
    normalized = rel_path.replace("\\", "/").lower()
    wrapped = f"/{normalized}"
    lower_name = Path(normalized).name.lower()
    if "client_component" in signals or "browser_runtime_surface" in signals:
        return False
    if _is_reference_or_non_next_surface(normalized):
        return False
    if "next/server" in content or "next/headers" in content or "next/cache" in content:
        return True
    if "/app/api/" in wrapped:
        return True
    if lower_name in {"route.ts", "route.js", "route.tsx", "page.ts", "page.tsx", "layout.ts", "layout.tsx"} and _is_next_path(normalized):
        return True
    if "/pages/" in wrapped or normalized.startswith("pages/"):
        return bool(re.search(r"\b(?:getServerSideProps|getStaticProps|getInitialProps)\b", content))
    return False


def _serialization_prop_contract_kind(content: str) -> str | None:
    found_callback = False
    for match in EXPORT_COMPONENT_PARAMS_RE.finditer(content):
        params = match.group("params") or ""
        if "{" not in params or ":" not in params:
            continue
        if SERIALIZATION_DATA_PROP_TOKEN_RE.search(params):
            return "data"
        if SERIALIZATION_CALLBACK_PROP_TOKEN_RE.search(params):
            found_callback = True
    for match in EXPORT_CONST_COMPONENT_PARAMS_RE.finditer(content):
        params = match.group("params") or ""
        if "}:" not in params and "}: {" not in params:
            continue
        if SERIALIZATION_DATA_PROP_TOKEN_RE.search(params):
            return "data"
        if SERIALIZATION_CALLBACK_PROP_TOKEN_RE.search(params):
            found_callback = True
    return "callback" if found_callback else None


def _build_risk_evidence(risks: list[str], signals: list[str]) -> dict[str, dict[str, str]]:
    signal_set = set(signals)
    evidence: dict[str, dict[str, str]] = {}
    for risk in sorted(set(risks)):
        if risk == "route_input_without_visible_validation_contract":
            if signal_set & {"mutation_surface", "env_boundary", "server_fetch_surface", "api_route_mutation_surface"}:
                evidence[risk] = {
                    "status": "confirmed_local_contract_missing",
                    "proof": "route reads request input in a privileged or side-effectful surface without a visible local schema/manual guard",
                }
            else:
                evidence[risk] = {
                    "status": "needs_transitive_helper_proof",
                    "proof": "route input appears delegated to another helper; validate the callee before treating this as a defect",
                }
        elif risk == "client_boundary_has_serialization_sensitive_prop_contract":
            evidence[risk] = {
                "status": "needs_parent_boundary_proof",
                "proof": "client component exposes non-serializable data props; confirm whether a server parent passes them across the RSC boundary",
            }
        elif risk == "client_server_boundary_mixed":
            evidence[risk] = {
                "status": "confirmed_local_boundary_mismatch",
                "proof": "client component locally references a private server-only boundary signal",
            }
        else:
            evidence[risk] = {
                "status": "confirmed_local_signal",
                "proof": "risk is supported by local file-level signals",
            }
    return evidence


def _candidate_import_targets(rel_path: str, source: str) -> list[str]:
    base = Path(rel_path).parent
    raw = (base / source).as_posix()
    candidates = [raw]
    if Path(raw).suffix:
        return candidates
    for suffix in (".ts", ".tsx", ".js", ".jsx"):
        candidates.append(f"{raw}{suffix}")
    for index_name in ("index.ts", "index.tsx", "index.js", "index.jsx"):
        candidates.append(f"{raw}/{index_name}")
    normalized: list[str] = []
    for item in candidates:
        parts: list[str] = []
        for part in item.split("/"):
            if part in {"", "."}:
                continue
            if part == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(part)
        normalized.append("/".join(parts))
    return normalized


def _relative_helper_validation_targets(rel_path: str, content: str, project_files: dict[str, str]) -> list[str]:
    targets: list[str] = []
    for match in RELATIVE_NAMED_IMPORT_RE.finditer(content):
        names = [name.strip().split(" as ")[-1].strip() for name in match.group("names").split(",")]
        called_names = [name for name in names if name and re.search(rf"\b{re.escape(name)}\s*\(", content)]
        if not called_names:
            continue
        for candidate in _candidate_import_targets(rel_path, match.group("source")):
            helper_content = project_files.get(candidate)
            if not helper_content:
                continue
            if (
                VALIDATION_CONTRACT_RE.search(helper_content)
                or MANUAL_INPUT_GUARD_RE.search(helper_content)
                or SIGNED_REQUEST_CONTRACT_RE.search(helper_content)
                or ROUTE_GUARD_CONTRACT_RE.search(helper_content)
                or TRANSITIVE_HELPER_VALIDATION_RE.search(helper_content)
            ):
                targets.append(candidate)
                break
    return sorted(set(targets))


def _normalize_dep_target(project: str, dep: str) -> tuple[str, str]:
    dep = str(dep or "").replace("\\", "/")
    if "::" in dep:
        pkey, rel = dep.split("::", 1)
        return pkey, rel
    return project, dep


def _atlas_value_mentions_next(value: Any) -> bool:
    if isinstance(value, str):
        return "next/" in value or value in {"next", "next-auth"}
    if isinstance(value, list):
        return any(_atlas_value_mentions_next(item) for item in value)
    if isinstance(value, dict):
        return any(_atlas_value_mentions_next(item) for item in value.values())
    return False


def _is_next_boundary_candidate(rel_path: str, fdata: Any) -> bool:
    normalized = str(rel_path or "").replace("\\", "/")
    if _is_next_path(normalized):
        return True
    if not isinstance(fdata, dict):
        return False
    for key in ("dependencies", "imports", "import_records", "external_deps", "ui_dependencies"):
        if _atlas_value_mentions_next(fdata.get(key)):
            return True
    return False


def _enrich_risk_evidence_with_context(
    rows: list[dict[str, Any]],
    project_files: dict[str, dict[str, str]],
    project_dependencies: dict[str, dict[str, list[str]]],
) -> None:
    reverse_deps: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for project, deps_by_file in project_dependencies.items():
        for source_file, deps in (deps_by_file or {}).items():
            for dep in deps or []:
                reverse_deps[_normalize_dep_target(project, dep)].append((project, source_file))

    for row in rows:
        project = str(row.get("project") or "")
        rel_path = str(row.get("file") or "")
        evidence = row.get("risk_evidence") or {}
        project_content = project_files.get(project, {})
        content = project_content.get(rel_path, "")

        serialization = evidence.get("client_boundary_has_serialization_sensitive_prop_contract")
        if serialization and serialization.get("status") == "needs_parent_boundary_proof":
            parents = reverse_deps.get((project, rel_path), [])
            if parents:
                client_parents = []
                server_parents = []
                for parent_project, parent_file in parents:
                    parent_content = project_files.get(parent_project, {}).get(parent_file, "")
                    if not parent_content:
                        parent_content = _read_project_file(parent_project, parent_file)
                        project_files.setdefault(parent_project, {})[parent_file] = parent_content
                    if CLIENT_DIRECTIVE_RE.search(parent_content):
                        client_parents.append(f"{parent_project}::{parent_file}")
                    else:
                        server_parents.append(f"{parent_project}::{parent_file}")
                if server_parents:
                    serialization["status"] = "needs_parent_prop_flow_proof"
                    serialization["proof"] = (
                        "server/non-client parents import this client component; confirm whether non-serializable props cross the RSC boundary: "
                        + ", ".join(server_parents[:5])
                    )
                elif client_parents:
                    serialization["status"] = "client_parent_boundary_observed"
                    serialization["proof"] = (
                        "known import parents are client components; non-serializable props may be confined to the client tree: "
                        + ", ".join(client_parents[:5])
                    )

        route_input = evidence.get("route_input_without_visible_validation_contract")
        if route_input and route_input.get("status") == "needs_transitive_helper_proof":
            helper_targets = _relative_helper_validation_targets(rel_path, content, project_content)
            if helper_targets:
                route_input["status"] = "transitive_helper_contract_observed"
                route_input["proof"] = "relative helper contains validation/auth/safe-url proof: " + ", ".join(helper_targets[:5])


def analyze_next_boundary_file(project: str, rel_path: str, content: str) -> dict[str, Any] | None:
    normalized = rel_path.replace("\\", "/")
    if not _is_next_path(normalized) and "next/" not in content and "next/navigation" not in content:
        return None

    lower_name = Path(normalized).name.lower()
    signals: list[str] = []
    risks: list[str] = []

    if _is_reference_or_non_next_surface(normalized) or "/api/mock/" in f"/{normalized.lower()}":
        signals.append("reference_or_mock_route_surface")
    if CLIENT_DIRECTIVE_RE.search(content):
        signals.append("client_component")
    server_module_directive = bool(SERVER_DIRECTIVE_RE.search(content))
    if server_module_directive:
        signals.append("server_module")
    inline_server_action = bool(SERVER_ACTION_RE.search(content))
    if inline_server_action or (
        server_module_directive
        and re.search(r"\bexport\s+(?:async\s+)?function\s+|\.action\s*\(|\bformAction\b|<form\b[^>]*\baction\s*=", content, re.DOTALL)
    ):
        signals.append("server_action")
    if FORM_ACTION_BINDING_RE.search(content):
        if "@remix-run/react" in content or "from \"@remix-run/react\"" in content or "from '@remix-run/react'" in content:
            signals.append("form_navigation_action")
        else:
            signals.append("form_action_binding")
    if FORM_NAVIGATION_ACTION_RE.search(content):
        signals.append("form_navigation_action")
    if ACTION_STATE_RE.search(content):
        signals.append("server_action_pending_contract")
    if ACTION_ERROR_RE.search(content) or ACTION_CLIENT_ERROR_STATE_RE.search(content):
        signals.append("server_action_error_contract")
    if ACTION_CLIENT_CONTRACT_RE.search(content):
        signals.append("server_action_client_contract")
        signals.append("server_action_error_contract")
    if FRAMEWORK_SERVER_FUNCTION_CONTRACT_RE.search(content) and ("'use server'" in content or '"use server"' in content):
        signals.append("framework_server_function_contract")
        signals.append("server_action_error_contract")
    if lower_name == "route.ts" or lower_name == "route.js":
        signals.append("route_handler")
    if METADATA_CONTRACT_RE.search(content):
        signals.append("metadata_contract")
    if CACHE_OR_STATIC_SEGMENT_RE.search(content):
        signals.append("cache_or_dynamic_segment")
    if DYNAMIC_NO_STATIC_CACHE_RE.search(content):
        signals.append("dynamic_no_static_cache")
    if FETCH_CACHE_RE.search(content) or RESPONSE_CACHE_CONTROL_RE.search(content):
        signals.append("fetch_cache_policy")
    if STATIC_CACHE_SIGNAL_RE.search(content):
        signals.append("static_cache_contract")
    edge_runtime = bool(EDGE_RUNTIME_RE.search(content))
    if edge_runtime:
        signals.append("edge_runtime")
    if NODE_RUNTIME_RE.search(content):
        signals.append("node_runtime")
    if NODE_ONLY_IMPORT_RE.search(content) or NODE_ONLY_API_RE.search(content):
        signals.append("node_only_runtime_api")
    route_context_params_surface = bool(ROUTE_CONTEXT_PARAMS_RE.search(content))
    input_surface = bool(INPUT_SURFACE_RE.search(content) or route_context_params_surface)
    if input_surface:
        signals.append("route_input_surface")
    if route_context_params_surface:
        signals.append("route_context_params_surface")
    if (
        VALIDATION_CONTRACT_RE.search(content)
        or MANUAL_INPUT_GUARD_RE.search(content)
        or FRAMEWORK_ROUTE_HANDLER_CONTRACT_RE.search(content)
        or SIGNED_REQUEST_CONTRACT_RE.search(content)
    ):
        signals.append("input_validation_contract")
    if ROUTE_GUARD_CONTRACT_RE.search(content):
        signals.append("route_guard_contract")
    read_only_route_helper = bool(READ_ONLY_ROUTE_HELPER_RE.search(content))
    mutation_call_surface = bool(
        MUTATION_CALL_RE.search(content)
        and lower_name in {"action.ts", "actions.ts", "route.ts", "route.js"}
        and not read_only_route_helper
    )
    if read_only_route_helper and "route_handler" in signals:
        signals.append("read_only_route_helper")
    mutation_surface = (
        "server_action" in signals
        or bool(MUTATION_METHOD_RE.search(content))
        or mutation_call_surface
    )
    if mutation_surface:
        signals.append("mutation_surface")
    if mutation_surface and "route_handler" in signals and "server_action" not in signals:
        signals.append("api_route_mutation_surface")
    if REVALIDATION_RE.search(content):
        signals.append("revalidation_contract")
    env_names = {match.group(1) for match in ENV_ACCESS_RE.finditer(content)}
    public_env_names = {name for name in env_names if name.startswith("NEXT_PUBLIC_") or name in {"NODE_ENV"}}
    private_env_names = env_names - public_env_names
    if public_env_names:
        signals.append("public_env_boundary")
    if private_env_names or ("process.env" in content and not env_names):
        signals.append("env_boundary")
    if re.search(r"\buse(?:Router|Params|SearchParams|Pathname)\s*\(", content):
        signals.append("navigation_hook")
    serialization_contract_kind = _serialization_prop_contract_kind(content) if CLIENT_DIRECTIVE_RE.search(content) else None
    if serialization_contract_kind == "data":
        signals.append("rsc_serialization_sensitive_props")
    elif serialization_contract_kind == "callback":
        signals.append("rsc_serialization_parent_boundary_proof_required")
    if CLIENT_RUNTIME_SURFACE_RE.search(content):
        signals.append("browser_runtime_surface")
    has_fetch_call = "fetch(" in content
    next_fetch_cache_scope = has_fetch_call and _is_next_fetch_cache_scope(normalized, content, signals)
    if has_fetch_call and ("client_component" in signals or "browser_runtime_surface" in signals):
        signals.append("browser_fetch_surface")
    elif next_fetch_cache_scope:
        signals.append("next_server_fetch_cache_scope")
        signals.append("server_fetch_surface")
        if "fetch_cache_policy" not in signals:
            signals.append("server_fetch_policy_unspecified")
    elif has_fetch_call:
        signals.append("non_next_fetch_surface")

    if (
        "client_component" in signals
        and ("server_action" in signals or "env_boundary" in signals)
        and "reference_or_mock_route_surface" not in signals
    ):
        risks.append("client_server_boundary_mixed")
    if (
        "form_action_binding" in signals
        and "server_action_pending_contract" not in signals
        and "reference_or_mock_route_surface" not in signals
    ):
        risks.append("form_action_without_visible_pending_contract")
    action_error_exempt_surface = (
        "reference_or_mock_route_surface" in signals
        or "revalidation_contract" in signals
        or "form_navigation_action" in signals
    )
    if (
        ("server_action" in signals or "form_action_binding" in signals)
        and "server_action_error_contract" not in signals
        and not action_error_exempt_surface
    ):
        risks.append("server_action_without_visible_error_contract")
    env_sensitive_route = (
        "mutation_surface" in signals
        or "node_only_runtime_api" in signals
        or (has_fetch_call and "client_component" not in signals)
        or "env_boundary" in signals
    )
    if (
        "route_handler" in signals
        and env_sensitive_route
        and "env_boundary" not in signals
        and "route_guard_contract" not in signals
        and "input_validation_contract" not in signals
    ):
        risks.append("route_handler_without_explicit_env_boundary")
    if (
        has_fetch_call
        and next_fetch_cache_scope
        and "fetch_cache_policy" not in signals
        and "cache_or_dynamic_segment" in signals
        and "static_cache_contract" not in signals
        and "dynamic_no_static_cache" not in signals
    ):
        risks.append("fetch_without_explicit_cache_policy")
    revalidation_sensitive_mutation = (
        "static_cache_contract" in signals
        or "cache_or_dynamic_segment" in signals
    )
    revalidation_exempt_surface = (
        "reference_or_mock_route_surface" in signals
        or "framework_server_function_contract" in signals
    )
    if (
        "mutation_surface" in signals
        and revalidation_sensitive_mutation
        and "revalidation_contract" not in signals
        and not revalidation_exempt_surface
    ):
        risks.append("mutation_without_visible_revalidation_contract")
    if (
        "static_cache_contract" in signals
        and "mutation_surface" in signals
        and "revalidation_contract" not in signals
        and not revalidation_exempt_surface
    ):
        risks.append("static_cache_mutation_without_tag_or_path_revalidation")
    if "metadata_contract" in signals and "client_component" in signals:
        risks.append("metadata_inside_client_boundary")
    if edge_runtime and "node_only_runtime_api" in signals:
        risks.append("edge_runtime_imports_node_only_api")
    if (
        input_surface
        and ("route_handler" in signals or "server_action" in signals)
        and "input_validation_contract" not in signals
        and "reference_or_mock_route_surface" not in signals
    ):
        risks.append("route_input_without_visible_validation_contract")
    if "rsc_serialization_sensitive_props" in signals:
        risks.append("client_boundary_has_serialization_sensitive_prop_contract")

    if not signals:
        return None

    tier = "high" if any(r in risks for r in {"client_server_boundary_mixed", "metadata_inside_client_boundary", "edge_runtime_imports_node_only_api"}) else (
        "medium" if risks else "low"
    )
    return {
        "project": project,
        "file": normalized,
        "signals": sorted(set(signals)),
        "risks": sorted(set(risks)),
        "risk_evidence": _build_risk_evidence(risks, signals),
        "risk_tier": tier,
        "boundary_contract": {
            "client_component": "client_component" in signals,
            "server_module": "server_module" in signals,
            "server_action": "server_action" in signals,
            "form_action_binding": "form_action_binding" in signals,
            "form_navigation_action": "form_navigation_action" in signals,
            "pending_contract": "server_action_pending_contract" in signals,
            "error_contract": "server_action_error_contract" in signals,
            "action_client_contract": "server_action_client_contract" in signals,
            "framework_server_function_contract": "framework_server_function_contract" in signals,
            "input_validation_contract": "input_validation_contract" in signals,
            "route_guard_contract": "route_guard_contract" in signals,
            "revalidation_contract": "revalidation_contract" in signals,
            "api_route_mutation_surface": "api_route_mutation_surface" in signals,
            "cache_contract": bool({"fetch_cache_policy", "cache_or_dynamic_segment", "static_cache_contract"} & set(signals)),
            "browser_fetch_surface": "browser_fetch_surface" in signals,
            "server_fetch_surface": "server_fetch_surface" in signals,
            "serialization_sensitive": "rsc_serialization_sensitive_props" in signals,
            "runtime": "edge" if "edge_runtime" in signals else ("nodejs" if "node_runtime" in signals else "default"),
        },
    }


def run_next_boundary_analyzer() -> dict[str, Any]:
    logger.info("Analyzing Next.js boundary contracts...")
    atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
    rows: list[dict[str, Any]] = []
    project_files: dict[str, dict[str, str]] = defaultdict(dict)
    project_dependencies: dict[str, dict[str, list[str]]] = defaultdict(dict)
    if isinstance(atlas, dict):
        for project, pdata in atlas.items():
            files = (pdata or {}).get("files", {}) if isinstance(pdata, dict) else {}
            for rel_path, fdata in files.items():
                if isinstance(fdata, dict):
                    project_dependencies[project][rel_path] = list(fdata.get("dependencies") or [])
                if not _is_next_boundary_candidate(rel_path, fdata):
                    continue
                content = _read_project_file(project, rel_path)
                project_files[project][rel_path] = content
                result = analyze_next_boundary_file(project, rel_path, content)
                if result:
                    rows.append(result)
    _enrich_risk_evidence_with_context(rows, dict(project_files), dict(project_dependencies))

    signal_counts = Counter(signal for row in rows for signal in row.get("signals", []))
    risk_counts = Counter(risk for row in rows for risk in row.get("risks", []))
    evidence_status_counts = Counter(
        evidence.get("status")
        for row in rows
        for evidence in (row.get("risk_evidence") or {}).values()
        if evidence.get("status")
    )
    contract_counts = Counter(
        key
        for row in rows
        for key, value in (row.get("boundary_contract") or {}).items()
        if value is True
    )
    by_project = defaultdict(int)
    for row in rows:
        by_project[row["project"]] += 1

    primary_limit = report_surface_limit("next_boundary_analysis.primary_files")
    summary = {
        "files_analyzed": len(rows),
        "signal_counts": dict(signal_counts),
        "risk_counts": dict(risk_counts),
        "evidence_status_counts": dict(evidence_status_counts),
        "boundary_contract_counts": dict(contract_counts),
        "high_risk": sum(1 for row in rows if row.get("risk_tier") == "high"),
        "medium_risk": sum(1 for row in rows if row.get("risk_tier") == "medium"),
        "by_project": dict(sorted(by_project.items())),
        "primary_report_file_limit": primary_limit,
        "truncated_in_primary_report": max(0, len(rows) - primary_limit),
        "full_artifact": "next_boundary_analysis_full.json",
    }
    payload = {
        "meta": {"kind": "next_boundary_analysis", "version": "v1"},
        "summary": summary,
        "files": rows,
    }
    full_payload = {
        **payload,
        "meta": {"kind": "next_boundary_analysis_full", "version": "v1", "source": "next_boundary_analysis"},
    }
    save_json_atomic(RAW_DIR / "next_boundary_analysis.json", payload)
    save_json_atomic(RAW_DIR / "next_boundary_analysis_full.json", full_payload)

    lines = [
        "# Next.js Boundary Analysis",
        "",
        f"- Files analyzed: `{payload['summary']['files_analyzed']}`",
        f"- High risk: `{payload['summary']['high_risk']}`",
        f"- Medium risk: `{payload['summary']['medium_risk']}`",
        "",
        "## Top Risks",
        "",
    ]
    if risk_counts:
        for risk, count in risk_counts.most_common():
            lines.append(f"- `{risk}`: `{count}`")
    else:
        lines.append("- No boundary risks detected.")
    lines.extend(["", "## Files", "", "| Project | File | Tier | Signals | Risks | Evidence |", "|---|---|---|---|---|---|"])
    for row in rows[:primary_limit]:
        evidence = row.get("risk_evidence") or {}
        evidence_statuses = sorted({item.get("status", "") for item in evidence.values() if item.get("status")})
        lines.append(
            f"| `{row['project']}` | `{row['file']}` | `{row['risk_tier']}` | "
            f"`{', '.join(row['signals'])}` | `{', '.join(row['risks']) or '-'}` | "
            f"`{', '.join(evidence_statuses) or '-'}` |"
        )
    save_text_atomic(REPORTS_DIR / "next_boundary_analysis.md", "\n".join(lines) + "\n")
    return payload


if __name__ == "__main__":
    run_next_boundary_analyzer()
