from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.operational_limits import react_v11_fixture_ast_timeout_seconds
from tools.engines.framework_route_analyzer import analyze_project_routes
from tools.engines.react_ecosystem_analyzer import analyze_react_ecosystem_file
from tools.engines.react_runtime_intelligence import analyze_runtime_intelligence_file
from tools.core.subprocess_telemetry import run_observed_subprocess


FIXTURE_ROOT = ROOT / "tools" / "tests" / "fixtures" / "react_v11"
SEQUENCER = ROOT / "tools" / "engines" / "ast_sequencer.cjs"
TAXONOMY = CONFIG_DIR / "react_fixture_family_taxonomy.json"
REACT_V11_CONTRACT_ID = "react_v11_contract_validation"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _sequence_fixture() -> dict[str, set[str]]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for React 1.0.0 extended AST fixture validation")
    files = sorted(
        path for path in FIXTURE_ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in {".ts", ".tsx", ".js", ".jsx"}
    )
    command = [node, str(SEQUENCER), "--batch-json", *[str(path) for path in files]]
    completed, _duration = run_observed_subprocess(
        command,
        cwd=ROOT,
        label="react_v11_fixture_batch_ast",
        timeout=react_v11_fixture_ast_timeout_seconds(),
        log=print,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout or "ast_sequencer.cjs failed")
    raw = json.loads(completed.stdout or "{}")
    features: dict[str, set[str]] = {}
    for path_text, symbols in raw.items():
        rel = Path(path_text).resolve().relative_to(FIXTURE_ROOT.resolve()).as_posix()
        meta = next(
            (item for item in symbols if isinstance(item, dict) and item.get("name") == "__file_meta__"),
            {},
        )
        features[rel] = {str(item) for item in meta.get("features", [])}
    return features


def _taxonomy_check() -> dict[str, Any]:
    payload = load_json_file(TAXONOMY, {})
    release_policy = payload.get("release_policy", {}) if isinstance(payload, dict) else {}
    included_releases = {
        str(item)
        for item in (release_policy.get("included_releases") or [])
        if str(item).strip()
    }
    validation_contract = payload.get("validation_contract", {}) if isinstance(payload, dict) else {}
    contract = validation_contract.get(REACT_V11_CONTRACT_ID, {}) if isinstance(validation_contract, dict) else {}
    required_family_ids = {
        str(item)
        for item in (contract.get("required_family_ids") or [])
        if str(item).strip()
    }
    families = {
        str(item.get("id")): item
        for item in payload.get("families", [])
        if isinstance(item, dict)
        and str(item.get("target_release") or "") in included_releases
    }
    linked = {
        family_id
        for family_id, item in families.items()
        if REACT_V11_CONTRACT_ID in (item.get("linked_existing_fixtures") or [])
    }
    missing = sorted(required_family_ids - set(families))
    not_proven = sorted(
        family_id for family_id in required_family_ids
        if family_id in families and families[family_id].get("support_level") != "proven"
    )
    unlinked = sorted(
        family_id for family_id in required_family_ids
        if family_id in families
        and REACT_V11_CONTRACT_ID not in (families[family_id].get("linked_existing_fixtures") or [])
    )
    undeclared_linked = sorted(linked - required_family_ids)
    return {
        "contract_declared": bool(required_family_ids),
        "included_releases": sorted(included_releases),
        "expected_families": sorted(required_family_ids),
        "missing": missing,
        "not_proven": not_proven,
        "unlinked": unlinked,
        "undeclared_linked": undeclared_linked,
    }


def validate_react_v11_contracts() -> dict[str, Any]:
    import hashlib
    fixture_atlas = {"files": {}}
    for path in FIXTURE_ROOT.rglob("*"):
        if path.is_file():
            rel = path.relative_to(FIXTURE_ROOT).as_posix()
            content = path.read_text(encoding="utf-8", errors="replace")
            fixture_atlas["files"][rel] = {"hash": hashlib.sha256(content.encode("utf-8")).hexdigest()}
    route_payload = analyze_project_routes("REACT_V11", FIXTURE_ROOT, fixture_atlas)
    routes = route_payload.get("routes", [])
    features = _sequence_fixture()

    def route_exists(framework: str, route: str, route_kind: str | None = None) -> bool:
        return any(
            item.get("framework") == framework
            and item.get("route") == route
            and (route_kind is None or item.get("route_kind") == route_kind)
            for item in routes
        )

    router_features = features.get("src/router.tsx", set())
    pattern_features = features.get("src/components/Patterns.tsx", set())
    broken_content = (FIXTURE_ROOT / "src" / "components" / "BrokenMutation.tsx").read_text(encoding="utf-8")
    broken_analysis = analyze_runtime_intelligence_file(
        "REACT_V11",
        "src/components/BrokenMutation.tsx",
        broken_content,
    ) or {}
    broken_rules = {
        str(item.get("risk") or item.get("rule") or "")
        for item in broken_analysis.get("findings", [])
        if isinstance(item, dict)
    }
    stale_content = (FIXTURE_ROOT / "src" / "components" / "StaleClosure.tsx").read_text(encoding="utf-8")
    stale_analysis = analyze_react_ecosystem_file(
        "REACT_V11",
        "src/components/StaleClosure.tsx",
        stale_content,
        {
            "symbols": [
                {
                    "name": "__file_meta__",
                    "features": sorted(features.get("src/components/StaleClosure.tsx", set())),
                }
            ]
        },
    ) or {}
    stale_rules = {
        str(item.get("risk") or item.get("rule") or "")
        for item in stale_analysis.get("findings", [])
        if isinstance(item, dict)
    }
    taxonomy = _taxonomy_check()

    checks = [
        _check(
            "next_pages_router_routes",
            route_exists("next_pages_router", "/", "page")
            and route_exists("next_pages_router", "/blog/:slug*", "page")
            and route_exists("next_pages_router", "/api/items/:id", "api_route"),
            route_payload.get("route_counts"),
        ),
        _check("next_app_pages_hybrid", bool(route_payload.get("hybrid_next_app_pages")), route_payload.get("route_counts")),
        _check(
            "react_router_v7_framework_mode",
            {"ReactRouterFrameworkMode", "RouterConfig"}.issubset(router_features),
            sorted(router_features),
        ),
        _check(
            "tanstack_router_file_based",
            route_exists("tanstack_router", "/posts/$postId", "file_route")
            and "TanStackRouterFileRoute" in features.get("src/routes/posts.$postId.tsx", set()),
            [item for item in routes if item.get("framework") == "tanstack_router"],
        ),
        _check(
            "tanstack_router_code_based",
            route_exists("tanstack_router", "/settings", "code_route")
            and "TanStackRouterCodeRoute" in router_features,
            [item for item in routes if item.get("framework") == "tanstack_router"],
        ),
        _check(
            "catch_all_routes",
            route_payload.get("segment_kind_counts", {}).get("optional_catch_all", 0) >= 1
            and route_payload.get("segment_kind_counts", {}).get("catch_all", 0) >= 1,
            route_payload.get("segment_kind_counts"),
        ),
        _check(
            "route_groups",
            route_payload.get("segment_kind_counts", {}).get("route_group", 0) >= 1,
            route_payload.get("segment_kind_counts"),
        ),
        _check(
            "not_found_fallback_route",
            route_exists("next_app_router", "/", "not_found_boundary"),
            route_payload.get("route_kind_counts"),
        ),
        _check("headless_component_pattern", "HeadlessComponentSurface" in pattern_features, sorted(pattern_features)),
        _check(
            "context_api",
            {"ReactContext", "ReactProvider"}.issubset(pattern_features),
            sorted(pattern_features),
        ),
        _check(
            "optimistic_update_mutation_queue",
            {"ReactOptimisticState", "TanStackOptimisticMutation", "TanStackMutationRollback"}.issubset(pattern_features)
            and "mutation_cache_or_feedback_gap" in broken_rules,
            {"features": sorted(pattern_features), "negative_fixture_rules": sorted(broken_rules)},
        ),
        _check(
            "stale_closure_dependency_contract",
            "Hook:EmptyDeps:useEffect" in features.get("src/components/StaleClosure.tsx", set())
            and "effect_lifecycle_or_stale_closure_risk" in stale_rules,
            {
                "features": sorted(features.get("src/components/StaleClosure.tsx", set())),
                "finding_rules": sorted(stale_rules),
            },
        ),
        _check(
            "v11_taxonomy_is_proven_and_linked",
            taxonomy["contract_declared"]
            and not taxonomy["missing"]
            and not taxonomy["not_proven"]
            and not taxonomy["unlinked"]
            and not taxonomy["undeclared_linked"],
            taxonomy,
        ),
    ]
    status = "PASS" if all(item["passed"] for item in checks) else "FAIL"
    payload = {
        "meta": {
            "kind": "react_v11_contract_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "fixture_root": FIXTURE_ROOT.relative_to(ROOT).as_posix(),
            "evidence_basis": "real_source_ast_and_route_analysis",
        },
        "summary": {
            "status": status,
            "families": len(taxonomy["expected_families"]),
            "checks": len(checks),
            "passed": sum(1 for item in checks if item["passed"]),
        },
        "checks": checks,
        "route_summary": {
            "route_counts": route_payload.get("route_counts"),
            "route_kind_counts": route_payload.get("route_kind_counts"),
            "segment_kind_counts": route_payload.get("segment_kind_counts"),
            "hybrid_next_app_pages": route_payload.get("hybrid_next_app_pages"),
        },
    }
    save_json_atomic(RAW_DIR / "react_v11_contract_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "react_v11_contract_validation.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# React V1.1 Contract Validation",
        "",
        "Real source fixtures are parsed through the production AST sequencer and framework route analyzer.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- families: `{summary.get('families')}`",
        f"- passed: `{summary.get('passed')}/{summary.get('checks')}`",
        "",
        "| Contract | Passed |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = validate_react_v11_contracts()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
