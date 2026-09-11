from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import DOCTRINE, RAW_DIR, save_json_atomic
from tools.core.architecture_blueprints import (
    blueprint_axes_valid,
    canonical_profile_id,
    canonical_profiles,
    effective_profile_ids,
    profile_aliases,
)
from tools.engines.architecture_oracle import build_architecture_oracle


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _fixture_atlas() -> dict[str, Any]:
    return {
        "MAIN": {
            "files": {
                "src/app/App.tsx": {"features": ["Feature:UIComponent"]},
                "src/pages/Home.tsx": {"features": ["Feature:UIComponent"]},
                "src/widgets/Nav/index.ts": {},
                "src/features/auth/LoginForm.tsx": {"features": ["Feature:UIComponent"]},
                "src/entities/user/model.ts": {},
                "src/shared/api/client.ts": {},
            },
            "dependencies": {
                "src/app/App.tsx": ["src/pages/Home.tsx", "src/shared/api/client.ts"],
                "src/pages/Home.tsx": ["src/widgets/Nav/index.ts", "src/features/auth/LoginForm.tsx"],
                "src/features/auth/LoginForm.tsx": ["src/entities/user/model.ts", "src/shared/api/client.ts"],
                "src/entities/user/model.ts": ["src/shared/api/client.ts"],
                "src/widgets/Nav/index.ts": ["src/shared/api/client.ts"],
                "src/shared/api/client.ts": [],
            },
        }
    }


def _next_fixture_atlas() -> dict[str, Any]:
    return {
        "MAIN": {
            "files": {
                "src/app/layout.tsx": {"features": ["ContractKind:next_app_runtime"]},
                "src/app/page.tsx": {"features": ["Feature:UIComponent"]},
                "src/app/api/auth/route.ts": {"features": ["ContractKind:next_route_handler"]},
                "src/app/loading.tsx": {"features": ["Feature:UIComponent"]},
                "src/server/api/root.ts": {},
            },
            "dependencies": {
                "src/app/layout.tsx": ["src/app/page.tsx"],
                "src/app/page.tsx": ["src/server/api/root.ts"],
                "src/app/api/auth/route.ts": ["src/server/api/root.ts"],
                "src/app/loading.tsx": [],
                "src/server/api/root.ts": [],
            },
        }
    }


def _static_app_fixture_atlas() -> dict[str, Any]:
    return {
        "MAIN": {
            "files": {
                "static/app/api.tsx": {"features": ["Feature:UIComponent"]},
                "static/app/main.tsx": {"features": ["Arch:Provider"]},
                "static/app/utils.ts": {},
                "src/sentry/services/app/model.py": {},
            },
            "dependencies": {
                "static/app/api.tsx": ["static/app/utils.ts"],
                "static/app/main.tsx": ["static/app/api.tsx"],
                "static/app/utils.ts": [],
                "src/sentry/services/app/model.py": [],
            },
        }
    }


def _minimal_fixture_atlas() -> dict[str, Any]:
    return {
        "MAIN": {
            "files": {
                "src/App.tsx": {"features": ["Feature:UIComponent"]},
                "src/main.tsx": {},
                "src/lib/client.ts": {},
            },
            "dependencies": {
                "src/main.tsx": ["src/App.tsx"],
                "src/App.tsx": ["src/lib/client.ts"],
                "src/lib/client.ts": [],
            },
        }
    }


def _package_library_fixture_atlas() -> dict[str, Any]:
    files = {
        "package.json": {},
        "packages/ui/package.json": {},
        "packages/ui/src/index.ts": {},
        "packages/ui/src/Button.tsx": {"features": ["Feature:UIComponent"]},
        "packages/ui/src/Input.tsx": {"features": ["Feature:UIComponent"]},
        "packages/forms/package.json": {},
        "packages/forms/src/index.ts": {},
        "packages/forms/src/useFormField.ts": {},
        "packages/theme/package.json": {},
        "packages/theme/src/index.ts": {},
        "packages/theme/src/tokens.ts": {},
        "packages/utils/package.json": {},
        "packages/utils/src/index.ts": {},
        "packages/utils/src/format.ts": {},
    }
    return {
        "MAIN": {
            "files": files,
            "dependencies": {
                "packages/ui/src/index.ts": ["packages/ui/src/Button.tsx", "packages/ui/src/Input.tsx"],
                "packages/forms/src/index.ts": ["packages/forms/src/useFormField.ts", "packages/theme/src/index.ts"],
                "packages/theme/src/index.ts": ["packages/theme/src/tokens.ts"],
                "packages/utils/src/index.ts": ["packages/utils/src/format.ts"],
            },
        }
    }


def _turborepo_saas_fixture_atlas() -> dict[str, Any]:
    return {
        "MAIN": {
            "files": {
                "turbo.json": {},
                "pnpm-workspace.yaml": {},
                "apps/web/package.json": {},
                "apps/web/app/layout.tsx": {"features": ["ContractKind:next_app_runtime"]},
                "apps/web/app/page.tsx": {"features": ["Feature:UIComponent"]},
                "apps/web/app/api/users/route.ts": {"features": ["ContractKind:next_route_handler"]},
                "apps/web/app/dashboard/page.tsx": {"features": ["Feature:UIComponent"]},
                "apps/admin/package.json": {},
                "apps/admin/app/layout.tsx": {"features": ["ContractKind:next_app_runtime"]},
                "apps/admin/app/page.tsx": {"features": ["Feature:UIComponent"]},
                "packages/auth/package.json": {},
                "packages/auth/src/index.ts": {},
                "packages/db/package.json": {},
                "packages/db/src/index.ts": {},
                "packages/ui/package.json": {},
                "packages/ui/src/index.ts": {},
            },
            "dependencies": {
                "apps/web/app/page.tsx": ["packages/auth/src/index.ts", "packages/ui/src/index.ts"],
                "apps/web/app/api/users/route.ts": ["packages/db/src/index.ts", "packages/auth/src/index.ts"],
                "apps/admin/app/page.tsx": ["packages/ui/src/index.ts"],
                "packages/auth/src/index.ts": ["packages/db/src/index.ts"],
            },
        }
    }


def _plugin_platform_fixture_atlas() -> dict[str, Any]:
    return {
        "MAIN": {
            "files": {
                "src/core/host.ts": {},
                "src/plugins/payments/index.ts": {},
                "src/plugins/payments/stripeProvider.ts": {},
                "src/plugins/auth/index.ts": {},
                "src/plugins/auth/oauthProvider.ts": {},
                "src/adapters/storage/index.ts": {},
                "src/providers/email/index.ts": {},
                "src/integrations/slack/index.ts": {},
                "src/integrations/github/index.ts": {},
                "src/extensions/editor/index.ts": {},
            },
            "dependencies": {
                "src/core/host.ts": [
                    "src/plugins/payments/index.ts",
                    "src/plugins/auth/index.ts",
                    "src/adapters/storage/index.ts",
                    "src/providers/email/index.ts",
                    "src/integrations/slack/index.ts",
                ],
                "src/plugins/payments/index.ts": ["src/plugins/payments/stripeProvider.ts"],
                "src/plugins/auth/index.ts": ["src/plugins/auth/oauthProvider.ts"],
            },
        }
    }


def _mixed_architecture_fixture_atlas() -> dict[str, Any]:
    return {
        "MAIN": {
            "files": {
                "apps/web/app/layout.tsx": {"features": ["ContractKind:next_app_runtime"]},
                "apps/web/app/page.tsx": {"features": ["Feature:UIComponent"]},
                "apps/web/app/api/search/route.ts": {"features": ["ContractKind:next_route_handler"]},
                "packages/ui/src/index.ts": {},
                "packages/ui/src/Button.tsx": {"features": ["Feature:UIComponent"]},
                "packages/auth/src/index.ts": {},
                "src/domain/user.ts": {},
                "src/application/usecases/createUser.ts": {},
                "src/infra/db/userRepo.ts": {},
                "src/adapters/http/userController.ts": {},
                "src/plugins/billing/index.ts": {},
                "src/plugins/billing/provider.ts": {},
            },
            "dependencies": {
                "apps/web/app/page.tsx": ["packages/ui/src/index.ts", "packages/auth/src/index.ts"],
                "apps/web/app/api/search/route.ts": ["src/adapters/http/userController.ts"],
                "src/adapters/http/userController.ts": ["src/application/usecases/createUser.ts"],
                "src/application/usecases/createUser.ts": ["src/domain/user.ts"],
                "src/infra/db/userRepo.ts": ["src/domain/user.ts"],
                "src/plugins/billing/index.ts": ["src/plugins/billing/provider.ts"],
            },
        }
    }


def _modular_spa_fixture_atlas() -> dict[str, Any]:
    files = {
        "src/main.tsx": {},
        "src/App.tsx": {"features": ["Feature:UIComponent"]},
        "src/components/Header.tsx": {"features": ["Feature:UIComponent"]},
        "src/components/Dashboard.tsx": {"features": ["Feature:UIComponent"]},
        "src/hooks/useSession.ts": {},
        "src/services/api.ts": {},
        "src/contexts/SessionContext.tsx": {},
        "src/utils/format.ts": {},
    }
    return {
        "MAIN": {
            "files": files,
            "dependencies": {
                "src/main.tsx": ["src/App.tsx"],
                "src/App.tsx": ["src/components/Header.tsx", "src/components/Dashboard.tsx", "src/contexts/SessionContext.tsx"],
                "src/components/Header.tsx": ["src/hooks/useSession.ts"],
                "src/components/Dashboard.tsx": ["src/services/api.ts", "src/utils/format.ts"],
                "src/hooks/useSession.ts": ["src/contexts/SessionContext.tsx"],
                "src/contexts/SessionContext.tsx": ["src/services/api.ts"],
            },
        }
    }


def _clean_architecture_fixture_atlas() -> dict[str, Any]:
    files: dict[str, Any] = {}
    dependencies: dict[str, list[str]] = {}
    for index in range(5):
        domain = f"src/domain/entity{index}.ts"
        application = f"src/application/usecase{index}.ts"
        port = f"src/application/ports/port{index}.ts"
        adapter = f"src/adapters/repository{index}.ts"
        ui = f"src/ui/view{index}.tsx"
        files.update({domain: {}, application: {}, port: {}, adapter: {}, ui: {"features": ["Feature:UIComponent"]}})
        dependencies[domain] = []
        dependencies[port] = [domain]
        dependencies[application] = [domain, port]
        dependencies[adapter] = [port, domain]
        dependencies[ui] = [application]
    return {"MAIN": {"files": files, "dependencies": dependencies}}


def _sovereign_hybrid_fixture_atlas() -> dict[str, Any]:
    files: dict[str, Any] = {}
    dependencies: dict[str, list[str]] = {}
    for index in range(4):
        shared = f"src/shared/api/client{index}.ts"
        entity = f"src/entities/entity{index}/model.ts"
        feature = f"src/features/feature{index}/ui.tsx"
        application = f"src/application/usecase{index}.ts"
        domain = f"src/domain/model{index}.ts"
        adapter = f"src/adapters/repository{index}.ts"
        files.update({shared: {}, entity: {}, feature: {"features": ["Feature:UIComponent"]}, application: {}, domain: {}, adapter: {}})
        dependencies[shared] = []
        dependencies[domain] = []
        dependencies[entity] = [shared, domain]
        dependencies[application] = [domain]
        dependencies[adapter] = [domain, application]
        dependencies[feature] = [entity, application, shared]
    return {"MAIN": {"files": files, "dependencies": dependencies}}


def run_validation() -> dict[str, Any]:
    oracle_source = (ROOT / "tools" / "engines" / "architecture_oracle.py").read_text(encoding="utf-8")
    oracle_policy = DOCTRINE.get("architecture_oracle_policy", {}) if isinstance(DOCTRINE, dict) else {}
    blueprint_markers = oracle_policy.get("blueprint_markers", {}) if isinstance(oracle_policy, dict) else {}
    payload = build_architecture_oracle(_fixture_atlas(), use_discovery_prior=False)
    project = payload["projects"][0] if payload.get("projects") else {}
    fsd = ((project.get("evidence") or {}).get("fsd") or {})
    next_payload = build_architecture_oracle(_next_fixture_atlas(), use_discovery_prior=False)
    next_project = next_payload["projects"][0] if next_payload.get("projects") else {}
    static_payload = build_architecture_oracle(_static_app_fixture_atlas(), use_discovery_prior=False)
    static_project = static_payload["projects"][0] if static_payload.get("projects") else {}
    static_evidence = static_project.get("evidence") or {}
    minimal_payload = build_architecture_oracle(_minimal_fixture_atlas(), use_discovery_prior=False)
    minimal_project = minimal_payload["projects"][0] if minimal_payload.get("projects") else {}
    package_payload = build_architecture_oracle(_package_library_fixture_atlas(), use_discovery_prior=False)
    package_project = package_payload["projects"][0] if package_payload.get("projects") else {}
    turbo_payload = build_architecture_oracle(_turborepo_saas_fixture_atlas(), use_discovery_prior=False)
    turbo_project = turbo_payload["projects"][0] if turbo_payload.get("projects") else {}
    plugin_payload = build_architecture_oracle(_plugin_platform_fixture_atlas(), use_discovery_prior=False)
    plugin_project = plugin_payload["projects"][0] if plugin_payload.get("projects") else {}
    mixed_payload = build_architecture_oracle(_mixed_architecture_fixture_atlas(), use_discovery_prior=False)
    mixed_project = mixed_payload["projects"][0] if mixed_payload.get("projects") else {}
    modular_payload = build_architecture_oracle(_modular_spa_fixture_atlas(), use_discovery_prior=False)
    modular_project = modular_payload["projects"][0] if modular_payload.get("projects") else {}
    clean_payload = build_architecture_oracle(_clean_architecture_fixture_atlas(), use_discovery_prior=False)
    clean_project = clean_payload["projects"][0] if clean_payload.get("projects") else {}
    sovereign_payload = build_architecture_oracle(_sovereign_hybrid_fixture_atlas(), use_discovery_prior=False)
    sovereign_project = sovereign_payload["projects"][0] if sovereign_payload.get("projects") else {}
    empty_payload = build_architecture_oracle(
        {"TOOLING": {"files": {}, "dependencies": {}}},
        use_discovery_prior=False,
    )
    empty_project = empty_payload["projects"][0] if empty_payload.get("projects") else {}
    profiles_source = (ROOT / "config" / "architecture_profiles.json").read_text(encoding="utf-8")
    auto_doctrine_source = (ROOT / "tools" / "auto_doctrine.py").read_text(encoding="utf-8")
    audit_rules_source = (ROOT / "tools" / "core" / "audit_rules.py").read_text(encoding="utf-8")
    canonical = canonical_profiles()
    aliases = profile_aliases()
    checks = [
        _check(
            "architecture_oracle_emits_post_atlas_payload",
            payload.get("meta", {}).get("input") == "post_atlas"
            and payload.get("meta", {}).get("mode") == "advisory_human_seal",
            payload.get("meta", {}),
        ),
        _check(
            "architecture_oracle_recommends_fsd_for_fsd_geometry",
            project.get("recommended_profile") == "FSD_STRICT" and project.get("confidence", 0) >= 0.7,
            project,
        ),
        _check(
            "architecture_oracle_measures_direction_and_encapsulation",
            fsd.get("direction_ratio") == 1.0 and fsd.get("encapsulation_ratio", 0) > 0,
            fsd,
        ),
        _check(
            "architecture_oracle_does_not_enforce_hard_gate",
            payload.get("summary", {}).get("hard_gate_enforced") is False
            and payload.get("policy", {}).get("audit_blocking_before_seal") is False,
            {"summary": payload.get("summary"), "policy": payload.get("policy")},
        ),
        _check(
            "architecture_oracle_detects_next_app_router_without_host_prior",
            next_project.get("recommended_profile") == "NEXTJS_APP_ROUTER"
            and next_project.get("confidence", 0) >= 0.8,
            next_project,
        ),
        _check(
            "architecture_oracle_does_not_treat_static_app_as_next_router",
            static_project.get("recommended_profile") != "NEXTJS_APP_ROUTER"
            and ((static_evidence.get("nextjs") or {}).get("route_convention_files") == 0),
            static_project,
        ),
        _check(
            "architecture_oracle_external_main_does_not_inherit_host_sovereign_prior",
            ((static_evidence.get("sovereign_hybrid") or {}).get("discovery_profile") or "") == ""
            and static_project.get("recommended_profile") != "SOVEREIGN_ELITE",
            static_project,
        ),
        _check(
            "architecture_oracle_plain_starter_remains_minimal",
            minimal_project.get("recommended_profile") == "MINIMAL"
            and minimal_project.get("confidence", 1) < 0.45,
            minimal_project,
        ),
        _check(
            "architecture_oracle_does_not_invent_a_blueprint_without_source_evidence",
            empty_project.get("classification_status") == "INSUFFICIENT_SOURCE_EVIDENCE"
            and empty_project.get("recommended_profile") is None
            and empty_project.get("confidence") == 0.0
            and empty_project.get("seal_ready") is False
            and (empty_project.get("seal_proposal") or {}).get("status") == "NOT_PROPOSED",
            empty_project,
        ),
        _check(
            "architecture_oracle_detects_package_library_monorepo",
            package_project.get("recommended_profile") == "MONOREPO_PACKAGE_LIBRARY"
            and package_project.get("confidence", 0) >= 0.55,
            package_project,
        ),
        _check(
            "architecture_oracle_detects_turborepo_saas",
            turbo_project.get("recommended_profile") == "TURBOREPO_SAAS"
            and turbo_project.get("confidence", 0) >= 0.55,
            turbo_project,
        ),
        _check(
            "architecture_oracle_detects_plugin_platform",
            plugin_project.get("recommended_profile") == "PLUGIN_PLATFORM"
            and plugin_project.get("confidence", 0) >= 0.55,
            plugin_project,
        ),
        _check(
            "architecture_oracle_can_report_mixed_architecture",
            mixed_project.get("recommended_profile") == "MIXED_ARCHITECTURE"
            and mixed_project.get("confidence", 0) >= 0.55,
            mixed_project,
        ),
        _check(
            "architecture_oracle_detects_modular_spa_without_overclaiming_fsd",
            modular_project.get("recommended_profile") == "MODULAR_FLAT"
            and modular_project.get("seal_ready") is False,
            modular_project,
        ),
        _check(
            "architecture_oracle_detects_clean_architecture_geometry",
            clean_project.get("recommended_profile") == "CLEAN_ARCHITECTURE"
            and clean_project.get("confidence", 0) >= 0.7,
            clean_project,
        ),
        _check(
            "architecture_oracle_detects_sovereign_hybrid_geometry",
            sovereign_project.get("recommended_profile") == "SOVEREIGN_ELITE"
            and sovereign_project.get("confidence", 0) >= 0.7,
            sovereign_project,
        ),
        _check(
            "architecture_oracle_blueprint_vocabulary_is_doctrine_driven",
            isinstance(blueprint_markers, dict)
            and bool(blueprint_markers.get("fsd_ranks"))
            and bool(blueprint_markers.get("clean_layers"))
            and bool(blueprint_markers.get("package_library_markers"))
            and bool(blueprint_markers.get("plugin_markers"))
            and "FSD_RANKS =" not in oracle_source
            and "CLEAN_LAYERS =" not in oracle_source
            and "PLUGIN_MARKERS =" not in oracle_source,
            {"blueprint_marker_keys": sorted(blueprint_markers.keys()) if isinstance(blueprint_markers, dict) else []},
        ),
        _check(
            "architecture_blueprint_registry_has_valid_canonical_axes",
            len(canonical) >= 10 and all(blueprint_axes_valid(profile) for profile in canonical),
            {"canonical_profiles": sorted(canonical)},
        ),
        _check(
            "architecture_blueprint_aliases_resolve_to_canonical_profiles",
            bool(aliases)
            and all(canonical_profile_id(alias) in canonical for alias in aliases)
            and canonical_profile_id("FSD_STANDARD") == "FSD_STRICT"
            and canonical_profile_id("HEXAGONAL_PURE") == "CLEAN_ARCHITECTURE"
            and canonical_profile_id("FRACTAL_SOVEREIGN_MONOLITH") == "SOVEREIGN_ELITE",
            aliases,
        ),
        _check(
            "architecture_blueprint_implications_preserve_rule_compatibility",
            {"SOVEREIGN_ELITE", "FSD_STRICT", "FSD_STANDARD", "CLEAN_ARCHITECTURE", "HEXAGONAL_PURE"}
            <= effective_profile_ids("SOVEREIGN_ELITE")
            and "FSD_STANDARD" in effective_profile_ids("NEXTJS_APP_ROUTER"),
            {
                "sovereign_effective": sorted(effective_profile_ids("SOVEREIGN_ELITE")),
                "next_effective": sorted(effective_profile_ids("NEXTJS_APP_ROUTER")),
            },
        ),
        _check(
            "architecture_blueprint_alias_ssot_is_not_duplicated_in_python",
            '"aliases"' in profiles_source
            and "PROFILE_ALIASES" not in auto_doctrine_source
            and "PROFILE_ALIASES" not in audit_rules_source,
            "Alias ownership belongs to config/architecture_profiles.json.",
        ),
        _check(
            "architecture_oracle_emits_canonical_blueprint_coordinates",
            project.get("blueprint", {}).get("canonical_profile") == "FSD_STRICT"
            and next_project.get("blueprint", {}).get("runtime") == "next_app_router"
            and package_project.get("blueprint", {}).get("repository_shape") == "package_library"
            and sovereign_project.get("blueprint", {}).get("composition_model") == "fractal_recursive",
            {
                "fsd": project.get("blueprint"),
                "next": next_project.get("blueprint"),
                "package": package_project.get("blueprint"),
                "sovereign": sovereign_project.get("blueprint"),
            },
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    result = {
        "meta": {"kind": "architecture_oracle_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "architecture_oracle_validation.json", result)
    return result


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
