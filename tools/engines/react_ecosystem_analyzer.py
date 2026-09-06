from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, DYNAMIC_CONFIG, RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.engine_progress import EngineProgress
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.logger import logger
from tools.core.react_evidence import atlas_evidence_kinds, attach_react_evidence_contract, first_pattern_line
from tools.core.report_surface_limits import report_surface_limit
from tools.core.source_evidence import read_atlas_bound_source
from tools.engines.react_source_scanner import call_snippets, component_body_snippets


REACT_EXTENSIONS = (".tsx", ".jsx", ".ts", ".js")
COMPONENT_EXTENSIONS = (".tsx", ".jsx")

COMPONENT_RE = re.compile(r"\b(?:export\s+)?(?:default\s+)?function\s+([A-Z][A-Za-z0-9_]*)\s*\(")
ARROW_COMPONENT_RE = re.compile(r"\b(?:export\s+)?(?:const|let)\s+([A-Z][A-Za-z0-9_]*)\s*=\s*(?:\([^)]*\)|[A-Za-z0-9_]+)\s*=>")
NESTED_COMPONENT_FUNCTION_RE = re.compile(r"\bfunction\s+([A-Z][A-Za-z0-9_]*)\s*\(")
NESTED_COMPONENT_ARROW_RE = re.compile(r"\b(?:const|let)\s+([A-Z][A-Za-z0-9_]*)\s*=\s*(?:\([^)]*\)|[A-Za-z0-9_]+)\s*=>")
HOOK_RE = re.compile(r"\b(use[A-Z][A-Za-z0-9_]*)\s*\(")
DEPENDENCY_ARRAY_RE = re.compile(r",\s*\[([^\]]*)\]\s*,?\s*\)\s*;?\s*$", re.DOTALL)
EFFECT_RESOURCE_SETUP_RE = re.compile(
    r"\b(?:setInterval|setTimeout|addEventListener|new\s+(?:ResizeObserver|MutationObserver|IntersectionObserver|WebSocket|EventSource)|\.subscribe\s*\()",
    re.MULTILINE,
)
EFFECT_RESOURCE_CLEANUP_RE = re.compile(
    r"\b(?:clearInterval|clearTimeout|removeEventListener|\.disconnect\s*\(|\.close\s*\(|\.unsubscribe\s*\(|return\s*\(\s*\)\s*=>|return\s+function)",
    re.MULTILINE,
)
MEMO_REACTIVE_READ_RE = re.compile(r"\b(?:props|params|router|searchParams|pathname|project|items|user|selected|id)\b")
IMPERATIVE_REACTIVE_READ_RE = re.compile(r"\b(?:props|params|project|items|user|selected|id|value|open|on[A-Z][A-Za-z0-9_]*)\b")
INLINE_CALLBACK_REF_RE = re.compile(r"\bref\s*=\s*{\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")
CONTEXT_PROVIDER_RE = re.compile(r"<([A-Z][A-Za-z0-9_]*(?:Context)?\.Provider|[A-Z][A-Za-z0-9_]*Provider)\b")
CONTEXT_PROVIDER_TAG_RE = re.compile(r"<(?:[A-Z][A-Za-z0-9_]*(?:Context)?\.Provider|[A-Z][A-Za-z0-9_]*Provider)\b[^>]*>", re.DOTALL)
INLINE_PROVIDER_VALUE_RE = re.compile(r"\bvalue\s*=\s*{\s*{", re.DOTALL)
INLINE_PROP_RE = re.compile(r"\b[A-Za-z_$][\w$]*\s*=\s*{\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")
INLINE_OBJECT_PROP_RE = re.compile(r"\b[A-Za-z_$][\w$]*\s*=\s*{{")
USE_STATE_RE = re.compile(r"\buseState\s*<[^>]+>\s*\(|\buseState\s*\(")
DERIVED_STATE_RE = re.compile(r"\buseState\s*\([^)]*(?:props\.|[A-Za-z0-9_]+\.map\(|[A-Za-z0-9_]+\.filter\()", re.DOTALL)
QUERY_KEY_RE = re.compile(r"\bqueryKey\s*:\s*(\[[^\]]*\]|[A-Za-z_$][\w$]*)", re.DOTALL)
USE_QUERY_RE = re.compile(r"\buse(?:Suspense)?Query\s*\(")
USE_MUTATION_RE = re.compile(r"\buseMutation\s*\(")
INVALIDATE_RE = re.compile(r"\binvalidateQueries\s*\(")
LAZY_COMPONENT_RE = re.compile(r"\b(?:React\.)?lazy\s*\(|\bdynamic\s*\(", re.MULTILINE)
LAZY_FALLBACK_RE = re.compile(r"<Suspense\b|\bfallback\s*=|\bloading\s*:", re.MULTILINE)
LAZY_ERROR_BOUNDARY_RE = re.compile(r"\b(?:ErrorBoundary|errorElement|componentDidCatch|getDerivedStateFromError|onError)\b")
POLICY_PATH = CONFIG_DIR / "react_ecosystem_policy.json"
_POLICY_CACHE: dict[str, Any] | None = None
FORM_HOOK_RE = re.compile(r"\buseForm\s*(?:<[^>]+>)?\s*\(")
VALIDATION_SCHEMA_RE = re.compile(r"\b(?:zodResolver|yupResolver|superstructResolver|valibotResolver|resolver\s*:|schema\s*:)")
SERVER_ACTION_FORM_RE = re.compile(r"\b(?:action|formAction)\s*=")
CONCURRENT_UX_RE = re.compile(r"\b(?:useTransition|startTransition|useOptimistic|useActionState|useFormStatus|useDeferredValue)\s*\(")
PENDING_FEEDBACK_RE = re.compile(r"\b(?:isPending|pending|isLoading|isSubmitting|disabled\s*=|aria-busy|loading)\b", re.IGNORECASE)
ASYNC_EVENT_HANDLER_RE = re.compile(r"\bon[A-Z][A-Za-z0-9_]*\s*=\s*{\s*async\b|\bon[A-Z][A-Za-z0-9_]*\s*=\s*{\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*async\b", re.DOTALL)
ASYNC_HANDLER_ERROR_RE = re.compile(r"\b(?:try\s*{|catch\s*\(|\.catch\s*\(|toast\.|setError\s*\(|error\s*=|onError\s*:)\b", re.IGNORECASE)
EXPENSIVE_LIST_DERIVATION_RE = re.compile(r"\.(?:filter|sort|map)\s*\([^)]*\)\s*\.(?:filter|sort|map)\s*\(", re.DOTALL)
INDEX_KEY_RE = re.compile(r"\bkey\s*=\s*{\s*(?:index|idx|i)\s*}")
VIRTUALIZATION_RE = re.compile(r"\b(?:useVirtualizer|FixedSizeList|VariableSizeList|Virtualized|react-window|react-virtual|virtualizer|useDeferredValue)\b")
FORM_CONTROL_RE = re.compile(r"<(?:input|select|textarea)\b([^>]*)>", re.IGNORECASE | re.DOTALL)
VALUE_PROP_RE = re.compile(r"\bvalue\s*=")
DEFAULT_VALUE_PROP_RE = re.compile(r"\bdefaultValue\s*=")
CHECKED_PROP_RE = re.compile(r"\bchecked\s*=")
DEFAULT_CHECKED_PROP_RE = re.compile(r"\bdefaultChecked\s*=")
CHANGE_CONTRACT_RE = re.compile(r"\b(?:onChange|readOnly|disabled)\s*=")
TOKEN_CLASS_RE = re.compile(
    r"className\s*=\s*(?:\"[^\"]*(?:#[0-9a-fA-F]{3,8}|px-\[[^\]]+\]|text-\[[^\]]+\]|bg-\[[^\]]+\]|style=)[^\"]*\"|'[^']*(?:#[0-9a-fA-F]{3,8}|px-\[[^\]]+\]|text-\[[^\]]+\]|bg-\[[^\]]+\]|style=)[^']*')"
)
STYLE_PROP_RE = re.compile(r"\bstyle\s*=\s*{{")
ACCESSIBILITY_GAP_RE = re.compile(r"<(?:div|span)\b(?=[^>]*\bonClick\s*=)(?![^>]*(?:role=|tabIndex=|onKeyDown=))", re.IGNORECASE)
ROUTE_FILE_RE = re.compile(r"(?:^|/)(?:app|pages)/.*(?:page|layout|route)\.(?:tsx|jsx|ts|js)$")
FEATURE_FLAG_RE = re.compile(
    r"\b(?:featureFlags?|flags?|isEnabled|useFeature|useFlag|growthbook|posthog|launchDarkly|process\.env\.(?:NEXT_PUBLIC_)?FEATURE|import\.meta\.env\.(?:VITE_)?FEATURE)\b",
    re.IGNORECASE,
)
HYDRATION_SENSITIVE_RE = re.compile(
    r"\b(?:Date\.now|new\s+Date\s*\(|Math\.random|crypto\.randomUUID|window\.|document\.|localStorage|sessionStorage|navigator\.)",
    re.MULTILINE,
)
HYDRATION_GUARD_RE = re.compile(r"\b(?:useEffect|useLayoutEffect|mounted|isClient|typeof\s+window|dynamic\s*\([^)]*ssr\s*:\s*false)", re.DOTALL)
GENERATED_CLIENT_RE = re.compile(
    r"\b(?:graphql|gql\s*`|openapi|swagger|prisma|generated/|__generated__|GraphQLClient|ApolloClient|createClient)\b",
    re.IGNORECASE,
)
GENERATED_BOUNDARY_RE = re.compile(r"\b(?:adapter|repository|service|clientBoundary|apiContract|schema|z\.object|safeParse)\b", re.IGNORECASE)


def _policy_string_list(section: dict[str, Any], key: str) -> list[str]:
    values = section.get(key, [])
    if not isinstance(values, list):
        return []
    return sorted({str(item).replace("\\", "/").strip() for item in values if str(item or "").strip()})


def _load_ecosystem_policy(force: bool = False) -> dict[str, Any]:
    global _POLICY_CACHE
    if _POLICY_CACHE is not None and not force:
        return _POLICY_CACHE

    configured = load_json_object_strict(POLICY_PATH, label="React ecosystem policy")

    reference = configured.get("reference_surfaces", {})
    if not isinstance(reference, dict):
        reference = {}
    providers = configured.get("data_cache_providers", {})
    if not isinstance(providers, dict):
        providers = {}
    compiled_providers: list[dict[str, Any]] = []
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        patterns = [str(item) for item in provider.get("patterns", []) if str(item or "").strip()]
        if not patterns:
            continue
        try:
            regex = re.compile("|".join(f"(?:{pattern})" for pattern in patterns), re.MULTILINE)
        except re.error:
            continue
        compiled_providers.append({
            "name": str(name),
            "risk": str(provider.get("risk") or "async_data_cache_contract_risk"),
            "action": str(provider.get("action") or ""),
            "regex": regex,
            "patterns": patterns,
        })

    default_data_cache = configured.get("default_data_cache", {})
    if not isinstance(default_data_cache, dict):
        default_data_cache = {}

    _POLICY_CACHE = {
        "meta": configured.get("meta", {"kind": "react_ecosystem_policy", "version": "default"}),
        "policy_source": str(POLICY_PATH),
        "reference_surfaces": {
            "docs_demo_path_markers": _policy_string_list(reference, "docs_demo_path_markers"),
            "test_fixture_path_markers": _policy_string_list(reference, "test_fixture_path_markers"),
            "test_fixture_file_suffixes": _policy_string_list(reference, "test_fixture_file_suffixes"),
        },
        "data_cache_providers": compiled_providers,
        "default_data_cache": {
            "risk": str(default_data_cache.get("risk") or "async_data_cache_contract_risk"),
            "action": str(
                default_data_cache.get("action")
                or "Map async data reads and writes to cache ownership, invalidation, rollback, and user feedback before moving this surface."
            ),
        },
    }
    return _POLICY_CACHE


def _path_has_marker(normalized: str, markers: list[str]) -> bool:
    parts = [part for part in normalized.split("/") if part]
    normalized_with_edges = f"/{normalized.strip('/')}/"
    for marker in markers:
        marker = marker.strip("/")
        if not marker:
            continue
        if "/" in marker and f"/{marker}/" in normalized_with_edges:
            return True
        if marker in parts:
            return True
    return False


def _project_root(project: str) -> Path:
    rel = (DYNAMIC_CONFIG.get("variations") or {}).get(project, "")
    return (ROOT / rel).resolve()


def _read_project_file(
    project: str,
    rel_path: str,
    atlas_entry: dict[str, Any] | None = None,
    cache: dict[str, str] | None = None,
) -> str:
    normalized = str(rel_path).replace("\\", "/")
    key = f"{project}::{normalized}"
    if cache is not None and key in cache:
        return cache[key]
    content = read_atlas_bound_source(
        component="react_ecosystem_analyzer",
        project=project,
        project_root=_project_root(project),
        rel_path=rel_path,
        atlas_entry=atlas_entry if isinstance(atlas_entry, dict) else {},
        reason="React ecosystem static contract scan",
    )
    value = content or ""
    if cache is not None:
        cache[key] = value
    return value


def _is_react_source(rel_path: str, content: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    if not normalized.endswith(REACT_EXTENSIONS):
        return False
    return (
        normalized.endswith(COMPONENT_EXTENSIONS)
        or "react" in content
        or "useState(" in content
        or "useEffect(" in content
        or "useQuery(" in content
        or "useForm(" in content
        or "<" in content and ">" in content
    )


def _component_names(content: str) -> list[str]:
    names = COMPONENT_RE.findall(content) + ARROW_COMPONENT_RE.findall(content)
    return sorted(set(names))


def _line_for(content: str, needle: str) -> int:
    idx = content.find(needle)
    if idx < 0:
        return 1
    return content[:idx].count("\n") + 1


def _first_line(content: str, *patterns: re.Pattern[str]) -> int:
    for pattern in patterns:
        line = first_pattern_line(content, pattern, default=0)
        if line:
            return line
    return 1


def _tier(score: int) -> str:
    if score >= 7:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def _source_surface_context(rel_path: str) -> str:
    normalized = rel_path.replace("\\", "/")
    reference = _load_ecosystem_policy().get("reference_surfaces", {})
    if _path_has_marker(normalized, reference.get("docs_demo_path_markers", []) or []):
        return "docs_demo"
    if _path_has_marker(normalized, reference.get("test_fixture_path_markers", []) or []) or normalized.endswith(
        tuple(reference.get("test_fixture_file_suffixes", []) or ())
    ):
        return "test_or_fixture"
    return "production_source"


def _data_cache_risk_name(content: str) -> str:
    policy = _load_ecosystem_policy()
    for provider in policy.get("data_cache_providers", []) or []:
        regex = provider.get("regex")
        if regex is not None and regex.search(content):
            return str(provider.get("risk") or "async_data_cache_contract_risk")
    return str(policy.get("default_data_cache", {}).get("risk") or "async_data_cache_contract_risk")


def _data_cache_action(content: str) -> str:
    policy = _load_ecosystem_policy()
    for provider in policy.get("data_cache_providers", []) or []:
        regex = provider.get("regex")
        if regex is not None and regex.search(content):
            return str(provider.get("action") or policy.get("default_data_cache", {}).get("action") or "")
    return str(policy.get("default_data_cache", {}).get("action") or "")


def _finding(
    project: str,
    rel_path: str,
    dimension: str,
    risk: str,
    evidence: str,
    score: int,
    action: str,
    line: int = 1,
    evidence_kinds: set[str] | None = None,
    evidence_scope: str | None = None,
) -> dict[str, Any]:
    source_context = _source_surface_context(rel_path)
    actionability = "reference_only" if source_context in {"docs_demo", "test_or_fixture"} else "production_actionable"
    production_relevance = "reference_or_fixture_surface" if actionability == "reference_only" else "production_surface"
    return attach_react_evidence_contract({
        "project": project,
        "file": rel_path.replace("\\", "/"),
        "line": line,
        "dimension": dimension,
        "risk": risk,
        "risk_tier": _tier(score),
        "score": score,
        "evidence": evidence,
        "source_context": source_context,
        "actionability": actionability,
        "production_relevance": production_relevance,
        "recommended_action": action,
        **({"evidence_scope": evidence_scope} if evidence_scope else {}),
    }, evidence_kinds={"static_regex"} | (evidence_kinds or set()), line=line)


def _map_render_snippets(content: str) -> list[str]:
    snippets: list[str] = []
    start = 0
    while True:
        idx = content.find(".map(", start)
        if idx < 0:
            break
        depth = 0
        end = idx
        for pos in range(idx + 4, min(len(content), idx + 1400)):
            char = content[pos]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth <= 0:
                    end = pos + 1
                    break
        snippet = content[idx:end] if end > idx else content[idx : idx + 500]
        if _looks_like_jsx_map_callback(snippet):
            snippets.append(snippet)
        start = max(idx + 5, end)
    return snippets


def _looks_like_jsx_map_callback(snippet: str) -> bool:
    """Separate rendered JSX lists from data-shaping maps with TS generics."""
    if "=>" not in snippet:
        return False
    if re.search(r"=>\s*\(?\s*<\s*(?:[A-Za-z][A-Za-z0-9_.:-]*|>)", snippet):
        return True
    if re.search(r"\breturn\s+\(?\s*<\s*(?:[A-Za-z][A-Za-z0-9_.:-]*|>)", snippet):
        return True
    return False


def _effect_call_snippets(content: str) -> list[str]:
    return call_snippets(content, r"\buse(?:Effect|LayoutEffect|InsertionEffect)\s*\(")


def _memo_call_snippets(content: str) -> list[str]:
    return call_snippets(content, r"\buse(?:Memo|Callback)\s*\(")


def _imperative_handle_snippets(content: str) -> list[str]:
    return call_snippets(content, r"\buseImperativeHandle\s*\(")


def analyze_react_ecosystem_file(project: str, rel_path: str, content: str, atlas_file: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not _is_react_source(rel_path, content):
        return None

    normalized = rel_path.replace("\\", "/")
    file_evidence_kinds = atlas_evidence_kinds(atlas_file)
    atlas_features = set(str(feature) for feature in ((atlas_file or {}).get("features") or []) if str(feature or "").strip())
    if not atlas_features:
        for symbol in (atlas_file or {}).get("symbols", []) or []:
            if isinstance(symbol, dict) and symbol.get("name") == "__file_meta__":
                atlas_features.update(str(feature) for feature in symbol.get("features", []) if str(feature or "").strip())
                break
    components = _component_names(content)
    hooks = sorted(set(HOOK_RE.findall(content)))
    findings: list[dict[str, Any]] = []

    state_count = len(USE_STATE_RE.findall(content))
    effect_count = len(re.findall(r"\buse(?:Effect|LayoutEffect|InsertionEffect)\s*\(", content))
    provider_count = len(CONTEXT_PROVIDER_RE.findall(content))
    inline_callbacks = len(INLINE_PROP_RE.findall(content))
    inline_objects = len(INLINE_OBJECT_PROP_RE.findall(content))

    render_score = 0
    render_evidence: list[str] = []
    if provider_count >= 2:
        render_score += 3
        render_evidence.append(f"{provider_count} provider boundaries")
    if inline_callbacks >= 4:
        render_score += 2
        render_evidence.append(f"{inline_callbacks} inline callback props")
    if inline_objects >= 2:
        render_score += 2
        render_evidence.append(f"{inline_objects} inline object props")
    if state_count >= 5:
        render_score += 2
        render_evidence.append(f"{state_count} local state hooks")
    if render_score:
        findings.append(
            _finding(
                project,
                normalized,
                "react_render_risk",
                "render_pressure_or_context_blast_radius",
                ", ".join(render_evidence),
                render_score,
                "Split provider scope, stabilize high-churn props, and isolate stateful leaf components before large imports.",
                line=_first_line(content, CONTEXT_PROVIDER_RE, INLINE_PROP_RE, INLINE_OBJECT_PROP_RE, USE_STATE_RE),
                evidence_kinds=file_evidence_kinds,
            )
        )

    nested_component_defs = 0
    for body in component_body_snippets(content, COMPONENT_RE, ARROW_COMPONENT_RE):
        nested_component_defs += len(NESTED_COMPONENT_FUNCTION_RE.findall(body))
        nested_component_defs += len(NESTED_COMPONENT_ARROW_RE.findall(body))
    if nested_component_defs:
        findings.append(
            _finding(
                project,
                normalized,
                "nested_component_contract",
                "render_scoped_component_definition_contract",
                f"{nested_component_defs} component definition(s) appear inside another component body",
                min(8, nested_component_defs * 4),
                "Hoist nested component definitions to module scope or memoize the render surface deliberately to avoid remount/state-reset behavior.",
                line=_first_line(content, NESTED_COMPONENT_FUNCTION_RE, NESTED_COMPONENT_ARROW_RE),
                evidence_kinds=file_evidence_kinds,
            )
        )

    inline_provider_values = sum(1 for tag in CONTEXT_PROVIDER_TAG_RE.findall(content) if INLINE_PROVIDER_VALUE_RE.search(tag))
    provider_topology_risks: list[str] = []
    provider_topology_score = 0
    if inline_provider_values:
        provider_topology_risks.append(f"{inline_provider_values} provider value prop(s) use inline object/function identity")
        provider_topology_score += min(8, inline_provider_values * 4)
    if provider_count >= 4 and "compose" not in content.lower() and "Providers" not in content:
        provider_topology_risks.append(f"{provider_count} provider boundaries without visible provider composition contract")
        provider_topology_score += 3
    if provider_topology_risks:
        findings.append(
            _finding(
                project,
                normalized,
                "context_value_contract",
                "provider_topology_or_value_stability_risk",
                "; ".join(provider_topology_risks),
                provider_topology_score,
                "Hoist provider values into stable memo contracts and make provider composition/order explicit before widening the blast radius.",
                line=_first_line(content, CONTEXT_PROVIDER_TAG_RE),
            )
        )

    ast_stale_closure_features = sorted(
        feature for feature in atlas_features if feature.startswith("Hook:StaleClosureRisk:")
    )
    if ast_stale_closure_features:
        findings.append(
            _finding(
                project,
                normalized,
                "hook_contract",
                "effect_lifecycle_or_stale_closure_risk",
                f"AST confirmed captured reactive values behind empty dependencies: {', '.join(ast_stale_closure_features)}",
                min(10, 4 + len(ast_stale_closure_features) * 2),
                "Add captured reactive values to the dependency contract or move the behavior behind an event/effect abstraction.",
                line=_first_line(content, HOOK_RE),
                evidence_kinds=set(file_evidence_kinds) | {"ast_span"},
            )
        )

    for full in _effect_call_snippets(content):
        effect_body = full
        has_resource_setup = bool(EFFECT_RESOURCE_SETUP_RE.search(effect_body))
        has_resource_cleanup = bool(EFFECT_RESOURCE_CLEANUP_RE.search(effect_body))
        risks: list[str] = []
        score = 0
        if "fetch(" in effect_body and "AbortController" not in effect_body:
            risks.append("fetch without abort/cleanup contract")
            score += 3
        if re.search(r"\bset[A-Z][A-Za-z0-9_]*\s*\(", effect_body) and "[]" in full:
            risks.append("mount-only effect mutates state")
            score += 2
        deps = DEPENDENCY_ARRAY_RE.search(full)
        if deps and not deps.group(1).strip() and re.search(r"\b(?:props|params|router|searchParams|pathname|project|id)\b", effect_body):
            risks.append("empty dependency array reads reactive values")
            score += 4
        if not deps:
            risks.append("effect has no explicit dependency array")
            score += 2
        if risks:
            findings.append(
                _finding(
                    project,
                    normalized,
                    "hook_contract",
                    "effect_lifecycle_or_stale_closure_risk",
                    "; ".join(risks),
                    score,
                    "Review effect ownership, cleanup, and dependency contract; prefer event handlers or data-query hooks where applicable.",
                    _line_for(content, full[:40]),
                )
            )
        if has_resource_setup and not has_resource_cleanup:
            findings.append(
                _finding(
                    project,
                    normalized,
                    "effect_cleanup_contract",
                    "subscription_or_timer_without_cleanup_contract",
                    "effect opens timer/listener/observer/socket/subscription without visible cleanup",
                    4,
                    "Return an effect cleanup that clears timers, removes listeners, disconnects observers, closes sockets, or unsubscribes resources.",
                    _line_for(content, full[:40]),
                )
            )

    memo_risks: list[str] = []
    memo_score = 0
    memo_without_deps = 0
    empty_deps_with_reactive_reads = 0
    for full in _memo_call_snippets(content):
        deps = DEPENDENCY_ARRAY_RE.search(full)
        if not deps:
            memo_without_deps += 1
            continue
        if not deps.group(1).strip() and MEMO_REACTIVE_READ_RE.search(full):
            empty_deps_with_reactive_reads += 1
    if memo_without_deps:
        memo_score += min(6, memo_without_deps * 3)
        memo_risks.append(f"{memo_without_deps} memo callback(s) lack explicit dependency array")
    if empty_deps_with_reactive_reads:
        memo_score += min(8, empty_deps_with_reactive_reads * 4)
        memo_risks.append(f"{empty_deps_with_reactive_reads} memo callback(s) read reactive values with empty dependencies")
    if memo_risks:
        findings.append(
            _finding(
                project,
                normalized,
                "memo_dependency_contract",
                "memo_or_callback_dependency_contract_risk",
                "; ".join(memo_risks),
                memo_score,
                "Align useMemo/useCallback dependencies with reactive reads, or remove memoization when it only hides stale closure risk.",
                line=_first_line(content, MEMO_REACTIVE_READ_RE),
            )
        )

    ref_risks: list[str] = []
    ref_score = 0
    imperative_without_deps = 0
    imperative_empty_deps_with_reactive_reads = 0
    for full in _imperative_handle_snippets(content):
        deps = DEPENDENCY_ARRAY_RE.search(full)
        if not deps:
            imperative_without_deps += 1
            continue
        if not deps.group(1).strip() and IMPERATIVE_REACTIVE_READ_RE.search(full):
            imperative_empty_deps_with_reactive_reads += 1
    inline_callback_refs = len(INLINE_CALLBACK_REF_RE.findall(content))
    if imperative_without_deps:
        ref_score += min(6, imperative_without_deps * 3)
        ref_risks.append(f"{imperative_without_deps} imperative handle(s) lack explicit dependency array")
    if imperative_empty_deps_with_reactive_reads:
        ref_score += min(8, imperative_empty_deps_with_reactive_reads * 4)
        ref_risks.append(f"{imperative_empty_deps_with_reactive_reads} imperative handle(s) read reactive values with empty dependencies")
    if inline_callback_refs and "useCallback(" not in content:
        ref_score += min(4, inline_callback_refs * 2)
        ref_risks.append(f"{inline_callback_refs} inline callback ref(s) without stable callback contract")
    if ref_risks:
        findings.append(
            _finding(
                project,
                normalized,
                "ref_imperative_contract",
                "unstable_imperative_ref_contract",
                "; ".join(ref_risks),
                ref_score,
                "Keep imperative handles dependency-safe and stabilize callback refs that attach imperative behavior.",
                evidence_scope="file_level_ref_contract",
            )
        )

    state_risks: list[str] = []
    state_score = 0
    if state_count >= 6 and any(token in content for token in ("useQuery(", "useMutation(", "useForm(", "useSearchParams(", "useParams(")):
        state_risks.append("local state coexists with URL/server/form state")
        state_score += 4
    if DERIVED_STATE_RE.search(content):
        state_risks.append("possible derived state stored locally")
        state_score += 3
    if "create(" in content and "zustand" in content and state_count:
        state_risks.append("store module also owns component-local state")
        state_score += 2
    if state_risks:
        findings.append(
            _finding(
                project,
                normalized,
                "state_ownership",
                "ambiguous_or_duplicated_state_owner",
                "; ".join(state_risks),
                state_score,
                "Declare one owner for URL, server cache, form, and local UI state before merging or refactoring.",
            )
        )

    query_count = len(USE_QUERY_RE.findall(content))
    mutation_count = len(USE_MUTATION_RE.findall(content))
    query_keys = QUERY_KEY_RE.findall(content)
    if query_count or mutation_count:
        query_score = 0
        query_risks: list[str] = []
        if query_count and not query_keys:
            query_score += 4
            query_risks.append("query call without visible queryKey contract")
        if mutation_count and "onSuccess" not in content and not INVALIDATE_RE.search(content):
            query_score += 4
            query_risks.append("mutation without visible invalidation/onSuccess contract")
        if any("[" in key and re.search(r"\b(?:id|projectId|params|searchParams)\b", content) and "undefined" not in content for key in query_keys):
            query_score += 2
            query_risks.append("query key likely depends on route/user input")
        if query_risks:
            findings.append(
                _finding(
                    project,
                    normalized,
                    "data_cache_flow",
                    _data_cache_risk_name(content),
                    "; ".join(query_risks),
                    query_score,
                    _data_cache_action(content),
                    line=_first_line(content, USE_QUERY_RE, USE_MUTATION_RE, QUERY_KEY_RE),
                )
            )

    if LAZY_COMPONENT_RE.search(content):
        lazy_score = 0
        lazy_risks: list[str] = []
        if not LAZY_FALLBACK_RE.search(content):
            lazy_score += 4
            lazy_risks.append("lazy/dynamic component without visible loading fallback")
        if not LAZY_ERROR_BOUNDARY_RE.search(content):
            lazy_score += 2
            lazy_risks.append("lazy/dynamic component without visible error boundary")
        if lazy_risks:
            findings.append(
                _finding(
                    project,
                    normalized,
                    "lazy_boundary_contract",
                    "lazy_component_without_loading_or_error_boundary",
                    "; ".join(lazy_risks),
                    lazy_score,
                    "Wrap lazy/dynamic surfaces with Suspense/loading fallback and an error boundary before treating the split point as production-safe.",
                    line=_first_line(content, LAZY_COMPONENT_RE),
                )
            )

    if FEATURE_FLAG_RE.search(content):
        findings.append(
            _finding(
                project,
                normalized,
                "runtime_branch_contract",
                "feature_flag_or_env_branch_needs_runtime_probe",
                "feature flag or environment-gated render branch detected",
                4,
                "Pair static review with ContextOS/local telemetry or a fixture that exercises enabled and disabled branches.",
                line=_first_line(content, FEATURE_FLAG_RE),
                evidence_kinds={"static_regex", "needs_runtime_proof"},
            )
        )

    if HYDRATION_SENSITIVE_RE.search(content) and not HYDRATION_GUARD_RE.search(content):
        findings.append(
            _finding(
                project,
                normalized,
                "hydration_consistency_contract",
                "hydration_sensitive_render_value",
                "render path reads time/random/browser-only value without visible client/hydration guard",
                5,
                "Move non-deterministic or browser-only reads behind a client effect, ssr:false boundary, or explicit hydration guard.",
                line=_first_line(content, HYDRATION_SENSITIVE_RE),
                evidence_kinds={"static_regex", "ssr_hydration"},
            )
        )

    if GENERATED_CLIENT_RE.search(content) and not GENERATED_BOUNDARY_RE.search(content):
        findings.append(
            _finding(
                project,
                normalized,
                "generated_client_contract",
                "generated_client_without_boundary_policy",
                "generated/API client signal appears without visible adapter/schema/boundary policy",
                4,
                "Wrap generated clients behind repository/adapter contracts and validate request/response shape before UI use.",
                line=_first_line(content, GENERATED_CLIENT_RE),
                evidence_kinds={"static_regex", "generated_code"},
            )
        )

    concurrency_score = 0
    concurrency_risks: list[str] = []
    has_concurrency_contract = bool(CONCURRENT_UX_RE.search(content))
    has_pending_feedback = bool(PENDING_FEEDBACK_RE.search(content))
    if (mutation_count or SERVER_ACTION_FORM_RE.search(content)) and not has_concurrency_contract:
        concurrency_score += 4
        concurrency_risks.append("async mutation/server action without transition/action-state contract")
    if (mutation_count or SERVER_ACTION_FORM_RE.search(content)) and not has_pending_feedback:
        concurrency_score += 3
        concurrency_risks.append("async user action without visible pending/disabled feedback")
    if EXPENSIVE_LIST_DERIVATION_RE.search(content) and "useDeferredValue" not in content and not has_concurrency_contract:
        concurrency_score += 3
        concurrency_risks.append("expensive list derivation without deferred/transition boundary")
    if concurrency_risks:
        findings.append(
            _finding(
                project,
                normalized,
                "concurrent_ux_contract",
                "async_or_expensive_interaction_without_concurrency_contract",
                "; ".join(concurrency_risks),
                concurrency_score,
                "Add transition/deferred/action-state/optimistic UX and pending affordances before treating the interaction as production-safe.",
                line=_first_line(content, USE_MUTATION_RE, SERVER_ACTION_FORM_RE, EXPENSIVE_LIST_DERIVATION_RE),
            )
        )

    async_handlers = len(ASYNC_EVENT_HANDLER_RE.findall(content))
    async_handler_score = 0
    async_handler_risks: list[str] = []
    if async_handlers and not ASYNC_HANDLER_ERROR_RE.search(content):
        async_handler_score += min(6, async_handlers * 3)
        async_handler_risks.append(f"{async_handlers} async event handler(s) without visible error handling")
    if async_handlers and not has_pending_feedback:
        async_handler_score += min(6, async_handlers * 2)
        async_handler_risks.append(f"{async_handlers} async event handler(s) without visible pending/disabled feedback")
    if async_handler_risks:
        findings.append(
            _finding(
                project,
                normalized,
                "async_event_contract",
                "async_user_event_without_error_or_pending_contract",
                "; ".join(async_handler_risks),
                async_handler_score,
                "Wrap async event handlers with explicit error handling and pending/disabled feedback to prevent silent failures and duplicate user actions.",
                line=_first_line(content, ASYNC_EVENT_HANDLER_RE),
            )
        )

    list_snippets = _map_render_snippets(content)
    if list_snippets:
        list_score = 0
        list_risks: list[str] = []
        missing_key = sum(1 for snippet in list_snippets if "key=" not in snippet)
        index_key = sum(1 for snippet in list_snippets if INDEX_KEY_RE.search(snippet))
        if missing_key:
            list_score += min(6, missing_key * 3)
            list_risks.append(f"{missing_key} mapped JSX list(s) without visible key")
        if index_key:
            list_score += min(4, index_key * 2)
            list_risks.append(f"{index_key} mapped JSX list(s) use index-like key")
        if len(list_snippets) >= 2 and not VIRTUALIZATION_RE.search(content):
            list_score += 2
            list_risks.append("multiple list render surfaces without visible virtualization/deferred boundary")
        if list_risks:
            findings.append(
                _finding(
                    project,
                    normalized,
                    "list_identity_contract",
                    "unstable_or_unbounded_list_render_contract",
                    "; ".join(list_risks),
                    list_score,
                    "Use stable domain keys and add virtualization/deferred rendering for large or repeated list surfaces.",
                    line=_line_for(content, list_snippets[0][:40]),
                )
            )

    has_form_surface = FORM_HOOK_RE.search(content) or "<form" in content or "ReactForm" in atlas_features
    has_validation_contract = VALIDATION_SCHEMA_RE.search(content) or bool({"FormResolver", "ValidationSchema", "ZodSchema"} & atlas_features)
    has_form_error_state = bool(re.search(r"\berrors\.", content)) or "FormErrorState" in atlas_features
    if has_form_surface:
        form_score = 0
        form_risks: list[str] = []
        if (FORM_HOOK_RE.search(content) or "ReactForm" in atlas_features) and not has_validation_contract:
            form_score += 4
            form_risks.append("form hook without resolver/schema contract")
        if SERVER_ACTION_FORM_RE.search(content) and "useFormState" not in content and "useActionState" not in content:
            form_score += 3
            form_risks.append("server action form without action-state/error mapping")
        if has_form_error_state and not re.search(r"aria-(?:invalid|describedby)", content):
            form_score += 2
            form_risks.append("form errors without visible aria error mapping")
        if form_risks:
            findings.append(
                _finding(
                    project,
                    normalized,
                    "form_validation",
                    "form_schema_or_error_mapping_gap",
                    "; ".join(form_risks),
                    form_score,
                    "Connect UI form state to schema validation, server result mapping, and accessible field errors.",
                    line=_first_line(content, FORM_HOOK_RE, SERVER_ACTION_FORM_RE),
                )
            )

    control_score = 0
    control_risks: list[str] = []
    mixed_value = 0
    mixed_checked = 0
    missing_change = 0
    for match in FORM_CONTROL_RE.finditer(content):
        attrs = match.group(1) or ""
        has_value = bool(VALUE_PROP_RE.search(attrs))
        has_default_value = bool(DEFAULT_VALUE_PROP_RE.search(attrs))
        has_checked = bool(CHECKED_PROP_RE.search(attrs))
        has_default_checked = bool(DEFAULT_CHECKED_PROP_RE.search(attrs))
        has_change_contract = bool(CHANGE_CONTRACT_RE.search(attrs))
        if has_value and has_default_value:
            mixed_value += 1
        if has_checked and has_default_checked:
            mixed_checked += 1
        if (has_value or has_checked) and not has_change_contract:
            missing_change += 1
    if mixed_value:
        control_score += min(6, mixed_value * 3)
        control_risks.append(f"{mixed_value} control(s) mix value and defaultValue")
    if mixed_checked:
        control_score += min(6, mixed_checked * 3)
        control_risks.append(f"{mixed_checked} control(s) mix checked and defaultChecked")
    if missing_change:
        control_score += min(6, missing_change * 2)
        control_risks.append(f"{missing_change} controlled control(s) lack onChange/readOnly/disabled")
    if control_risks:
        findings.append(
            _finding(
                project,
                normalized,
                "form_control_contract",
                "controlled_uncontrolled_input_contract_risk",
                "; ".join(control_risks),
                control_score,
                "Choose controlled or uncontrolled ownership per field and wire onChange/readOnly/disabled explicitly.",
                line=_first_line(content, FORM_CONTROL_RE),
            )
        )

    design_score = 0
    design_risks: list[str] = []
    token_drift = len(TOKEN_CLASS_RE.findall(content))
    style_props = len(STYLE_PROP_RE.findall(content))
    if token_drift:
        design_score += min(5, token_drift * 2)
        design_risks.append(f"{token_drift} token bypass className patterns")
    if style_props >= 2:
        design_score += 2
        design_risks.append(f"{style_props} inline style objects")
    if design_risks:
        findings.append(
            _finding(
                project,
                normalized,
                "design_system_drift",
                "token_or_variant_contract_drift",
                "; ".join(design_risks),
                design_score,
                "Map visual choices to design tokens/component variants before importing the UI surface.",
                line=_first_line(content, TOKEN_CLASS_RE, STYLE_PROP_RE),
            )
        )

    a11y_gaps = len(ACCESSIBILITY_GAP_RE.findall(content))
    if a11y_gaps:
        findings.append(
            _finding(
                project,
                normalized,
                "accessibility_semantics",
                "clickable_non_semantic_element",
                f"{a11y_gaps} clickable div/span elements lack role/tabIndex/keyboard contract",
                min(8, a11y_gaps * 3),
                "Use semantic controls or add role, tabIndex, keyboard handler, and focus styling.",
                line=_first_line(content, ACCESSIBILITY_GAP_RE),
            )
        )

    if ROUTE_FILE_RE.search(normalized):
        route_score = 0
        route_risks: list[str] = []
        if "error.tsx" not in normalized and "ErrorBoundary" not in content and "try {" not in content and ("await " in content or "fetch(" in content):
            route_score += 3
            route_risks.append("route performs async work without visible error boundary/recovery")
        if "loading.tsx" not in normalized and "Suspense" not in content and ("useQuery(" in content or "await " in content):
            route_score += 2
            route_risks.append("route data path without visible loading boundary")
        if route_risks:
            findings.append(
                _finding(
                    project,
                    normalized,
                    "route_user_journey",
                    "route_lifecycle_gap",
                    "; ".join(route_risks),
                    route_score,
                    "Pair this route with loading/error/fallback journey contracts before treating it as safe to merge.",
                    line=_first_line(content, USE_QUERY_RE),
                )
            )

    if file_evidence_kinds:
        findings = [
            attach_react_evidence_contract(
                dict(item),
                evidence_kinds=set(item.get("evidence_kinds", [])) | file_evidence_kinds,
                line=int(item.get("line", 1) or 1),
                current_confidence=str(item.get("confidence") or ""),
            )
            for item in findings
        ]

    return {
        "project": project,
        "file": normalized,
        "components": components,
        "hooks": hooks,
        "counts": {
            "components": len(components),
            "hooks": len(hooks),
            "state_hooks": state_count,
            "effect_hooks": effect_count,
            "providers": provider_count,
            "queries": query_count,
            "mutations": mutation_count,
        },
        "findings": findings,
    }


def _merge_intelligence(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        if finding.get("project") == "MAIN":
            continue
        grouped[(str(finding.get("project")), str(finding.get("file")))].append(finding)

    rows: list[dict[str, Any]] = []
    for (project, rel_path), items in grouped.items():
        dims = sorted({str(item.get("dimension")) for item in items})
        score = sum(int(item.get("score", 0)) for item in items)
        high = sum(1 for item in items if item.get("risk_tier") == "high")
        if high or score >= 12:
            recommendation = "import_with_review"
        elif score >= 6:
            recommendation = "import_with_contract_tests"
        else:
            recommendation = "static_gate_sufficient"
        rows.append(
            {
                "source": project,
                "file": rel_path,
                "dimensions": dims,
                "risk_score": score,
                "high_risk_findings": high,
                "recommendation": recommendation,
                "required_gates": _required_gates_for_dimensions(dims),
            }
        )
    return sorted(rows, key=lambda row: (-row["risk_score"], row["source"], row["file"]))[:120]


def _required_gates_for_dimensions(dimensions: list[str]) -> list[str]:
    gates = {"static_quality_gate"}
    if any(
        dim in dimensions
        for dim in (
            "react_render_risk",
            "design_system_drift",
            "accessibility_semantics",
            "route_user_journey",
            "lazy_boundary_contract",
            "context_value_contract",
            "nested_component_contract",
        )
    ):
        gates.add("browser_smoke")
    if "data_cache_flow" in dimensions:
        gates.add("query_invalidation_review")
    if "form_validation" in dimensions:
        gates.add("form_schema_and_a11y_review")
    if "hook_contract" in dimensions:
        gates.add("hook_lifecycle_review")
    if "effect_cleanup_contract" in dimensions:
        gates.add("effect_cleanup_review")
    if "state_ownership" in dimensions:
        gates.add("state_owner_decision")
    if "concurrent_ux_contract" in dimensions:
        gates.add("concurrency_pending_ux_review")
    if "async_event_contract" in dimensions:
        gates.add("async_event_error_pending_review")
    if "list_identity_contract" in dimensions:
        gates.add("list_key_and_virtualization_review")
    if "form_control_contract" in dimensions:
        gates.add("controlled_form_contract_review")
    if "lazy_boundary_contract" in dimensions:
        gates.add("lazy_loading_error_boundary_review")
    if "context_value_contract" in dimensions:
        gates.add("context_value_stability_review")
    if "memo_dependency_contract" in dimensions:
        gates.add("memo_dependency_review")
    if "ref_imperative_contract" in dimensions:
        gates.add("imperative_ref_contract_review")
    if "nested_component_contract" in dimensions:
        gates.add("component_identity_review")
    return sorted(gates)


def run_react_ecosystem_analyzer() -> dict[str, Any]:
    logger.info("Analyzing React ecosystem surgical contracts...")
    started = time.perf_counter()
    progress = EngineProgress("react_ecosystem_analyzer")
    progress.start()
    atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
    atlas_loaded_at = time.perf_counter()
    files: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    source_cache: dict[str, str] = {}
    project_profile: dict[str, dict[str, int]] = {}

    if isinstance(atlas, dict):
        for project, pdata in atlas.items():
            project_started = time.perf_counter()
            project_files = (pdata or {}).get("files", {}) if isinstance(pdata, dict) else {}
            project_scanned = 0
            project_findings_before = len(findings)
            progress.phase("project_scan", total=len(project_files), project=project)
            for file_index, (rel_path, fmeta) in enumerate(project_files.items(), start=1):
                content = _read_project_file(
                    project,
                    str(rel_path),
                    fmeta if isinstance(fmeta, dict) else {},
                    source_cache,
                )
                row = analyze_react_ecosystem_file(project, str(rel_path), content, fmeta if isinstance(fmeta, dict) else None)
                if not row:
                    progress.advance(file_index, project=project, current_file=str(rel_path))
                    continue
                project_scanned += 1
                files.append({key: value for key, value in row.items() if key != "findings"})
                findings.extend(row["findings"])
                progress.advance(file_index, project=project, current_file=str(rel_path))
            project_profile[project] = {
                "candidate_files": len(project_files),
                "react_files": project_scanned,
                "findings": len(findings) - project_findings_before,
                "seconds": round(time.perf_counter() - project_started, 3),
            }
            logger.info("[REACT_ECOSYSTEM_PROFILE] project=%s %s", project, json.dumps(project_profile[project], sort_keys=True))
            progress.checkpoint("project_complete", project=project, **project_profile[project])

    dimension_counts = Counter(str(item.get("dimension")) for item in findings)
    risk_counts = Counter(str(item.get("risk")) for item in findings)
    tier_counts = Counter(str(item.get("risk_tier")) for item in findings)
    by_project = Counter(str(item.get("project")) for item in findings)
    merge_rows = _merge_intelligence(findings)

    sorted_findings = sorted(findings, key=lambda item: (-int(item.get("score", 0)), item.get("project", ""), item.get("file", "")))
    summary = {
        "files_analyzed": len(files),
        "findings": len(findings),
        "dimension_counts": dict(dimension_counts),
        "risk_counts": dict(risk_counts),
        "risk_tiers": dict(tier_counts),
        "high_risk": tier_counts.get("high", 0),
        "medium_risk": tier_counts.get("medium", 0),
        "by_project": dict(sorted(by_project.items())),
        "merge_intelligence_candidates": len(merge_rows),
    }
    primary_limit = report_surface_limit("react_ecosystem_analysis.primary_findings")
    payload = {
        "meta": {"kind": "react_ecosystem_analysis", "version": "v1"},
        "summary": {
            **summary,
            "truncated_in_primary_report": max(0, len(sorted_findings) - primary_limit),
            "primary_report_finding_limit": primary_limit,
            "full_artifact": "react_ecosystem_findings_full.json",
        },
        "files": files,
        "findings": sorted_findings[:primary_limit],
        "merge_intelligence": merge_rows,
    }
    full_findings_payload = {
        "meta": {"kind": "react_ecosystem_findings_full", "version": "v1"},
        "summary": {
            **summary,
            "truncated_in_primary_report": 0,
            "primary_report_finding_limit": len(sorted_findings),
            "full_artifact": "react_ecosystem_findings_full.json",
        },
        "findings": sorted_findings,
    }
    progress.phase("artifact_write", total=2)
    save_json_atomic(RAW_DIR / "react_ecosystem_analysis.json", payload)
    progress.advance(1, artifact="react_ecosystem_analysis.json")
    save_json_atomic(RAW_DIR / "react_ecosystem_findings_full.json", full_findings_payload)
    progress.advance(2, artifact="react_ecosystem_findings_full.json")
    save_text_atomic(REPORTS_DIR / "react_ecosystem_analysis.md", _render_markdown(payload))
    logger.info(
        "[REACT_ECOSYSTEM_PROFILE] %s",
        json.dumps(
            {
                "atlas_load_seconds": round(atlas_loaded_at - started, 3),
                "scan_seconds": round(time.perf_counter() - atlas_loaded_at, 3),
                "files_analyzed": len(files),
                "findings": len(findings),
                "projects": project_profile,
            },
            sort_keys=True,
        ),
    )
    progress.complete("PASS", projects=len(project_profile), files_analyzed=len(files), findings=len(findings))
    return payload


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# React Ecosystem Surgical Analysis",
        "",
        f"- Files analyzed: `{summary.get('files_analyzed', 0)}`",
        f"- Findings: `{summary.get('findings', 0)}`",
        f"- High risk: `{summary.get('high_risk', 0)}`",
        f"- Medium risk: `{summary.get('medium_risk', 0)}`",
        f"- Variation merge candidates with React gates: `{summary.get('merge_intelligence_candidates', 0)}`",
        "",
        "## Dimensions",
        "",
        "| Dimension | Count |",
        "|---|---:|",
    ]
    for dimension, count in Counter(summary.get("dimension_counts", {}) or {}).most_common():
        lines.append(f"| `{dimension}` | {count} |")

    lines.extend(["", "## Top Findings", "", "| Project | File | Tier | Dimension | Risk | Evidence | Action |", "|---|---|---|---|---|---|---|"])
    for item in payload.get("findings", [])[:80]:
        lines.append(
            f"| `{item.get('project')}` | `{item.get('file')}` | `{item.get('risk_tier')}` | "
            f"`{item.get('dimension')}` | `{item.get('risk')}` | {item.get('evidence', '-')} | {item.get('recommended_action', '-')} |"
        )

    lines.extend(["", "## Variation Merge Gates", "", "| Source | File | Score | Recommendation | Required Gates |", "|---|---|---:|---|---|"])
    for row in payload.get("merge_intelligence", [])[:80]:
        lines.append(
            f"| `{row.get('source')}` | `{row.get('file')}` | {row.get('risk_score', 0)} | "
            f"`{row.get('recommendation')}` | `{', '.join(row.get('required_gates', []))}` |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    run_react_ecosystem_analyzer()
