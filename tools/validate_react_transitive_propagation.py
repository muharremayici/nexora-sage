from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.engines.state_flow_scanner import build_state_flow_results


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _fixture_atlas() -> dict[str, Any]:
    return {
        "APP": {
            "files": {
                "features/auth/useAuthorQuery.ts": {
                    "features": ["QueryKey:queryKeys.author.details(id)", "Tech:selector"],
                    "state_flow": {
                        "has_zustand_store": False,
                        "query_keys": ["queryKeys.author.details(id)"],
                        "mutation_keys": [],
                        "client_actions": [],
                        "zustand_consumers": [],
                        "zustand_no_selector_calls": [],
                        "zustand_broad_selector_calls": [],
                        "technologies": ["tanstack_query"],
                    },
                    "symbols": [
                        {"name": "useAuthor", "type": "Hook", "exported": True},
                    ],
                    "exports": ["useAuthor"],
                    "import_records": [],
                },
                "pages/AuthorPage.tsx": {
                    "features": ["Feature:UIComponent"],
                    "state_flow": {
                        "has_zustand_store": False,
                        "query_keys": [],
                        "mutation_keys": [],
                        "client_actions": [],
                        "zustand_consumers": [],
                        "zustand_no_selector_calls": [],
                        "zustand_broad_selector_calls": [],
                        "technologies": [],
                    },
                    "symbols": [
                        {"name": "AuthorPage", "type": "Function", "exported": True},
                    ],
                    "exports": ["AuthorPage"],
                    "import_records": [
                        {
                            "source": "features/auth/useAuthorQuery.ts",
                            "raw_source": "../features/auth/useAuthorQuery",
                            "name": "useAuthor",
                            "kind": "named",
                        }
                    ],
                },
                "features/auth/useAuthorSession.ts": {
                    "features": ["ZustandStore", "Zustand:NoSelector", "Zustand:NoSelector:useAuthorStore"],
                    "state_flow": {
                        "has_zustand_store": True,
                        "query_keys": [],
                        "mutation_keys": [],
                        "client_actions": [],
                        "zustand_consumers": ["useAuthorStore"],
                        "zustand_no_selector_calls": ["useAuthorStore"],
                        "zustand_broad_selector_calls": [],
                        "technologies": ["zustand"],
                    },
                    "symbols": [
                        {"name": "useAuthorSession", "type": "Hook", "exported": True},
                    ],
                    "exports": ["useAuthorSession"],
                    "import_records": [],
                },
                "pages/AuthorShell.tsx": {
                    "features": ["Feature:UIComponent"],
                    "state_flow": {
                        "has_zustand_store": False,
                        "query_keys": [],
                        "mutation_keys": [],
                        "client_actions": [],
                        "zustand_consumers": [],
                        "zustand_no_selector_calls": [],
                        "zustand_broad_selector_calls": [],
                        "technologies": [],
                    },
                    "symbols": [
                        {"name": "AuthorShell", "type": "Function", "exported": True},
                    ],
                    "exports": ["AuthorShell"],
                    "import_records": [
                        {
                            "source": "features/auth/useAuthorSession.ts",
                            "raw_source": "../features/auth/useAuthorSession",
                            "name": "useAuthorSession",
                            "kind": "named",
                        }
                    ],
                },
            }
        }
    }


def run_validation() -> dict[str, Any]:
    results = build_state_flow_results(_fixture_atlas(), {})
    consumer = "APP::pages/AuthorPage.tsx"
    provider = "APP::features/auth/useAuthorQuery.ts"
    zustand_consumer = "APP::pages/AuthorShell.tsx"
    zustand_provider = "APP::features/auth/useAuthorSession.ts"
    checks = [
        _check(
            "consumer_inherits_query_key_from_custom_hook",
            "queryKeys.author.details(id)" in (results.get("tanstack_queries", {}).get(consumer) or []),
            results.get("tanstack_queries", {}),
        ),
        _check(
            "consumer_gets_boundary_signal",
            consumer in (results.get("boundary_signals") or {}),
            results.get("boundary_signals", {}).get(consumer),
        ),
        _check(
            "consumer_records_transitive_hook_source",
            any(
                item.get("hook") == "useAuthor" and item.get("provider") == provider
                for item in (results.get("transitive_hook_consumers", {}).get(consumer) or [])
            ),
            results.get("transitive_hook_consumers", {}).get(consumer),
        ),
        _check(
            "provider_remains_stateful",
            "queryKeys.author.details(id)" in (results.get("tanstack_queries", {}).get(provider) or []),
            results.get("tanstack_queries", {}).get(provider),
        ),
        _check(
            "consumer_inherits_zustand_consumer_from_custom_hook",
            "useAuthorStore" in (results.get("zustand_consumers", {}).get(zustand_consumer) or []),
            results.get("zustand_consumers", {}),
        ),
        _check(
            "consumer_records_transitive_zustand_hook_source",
            any(
                item.get("hook") == "useAuthorSession" and item.get("provider") == zustand_provider
                for item in (results.get("transitive_hook_consumers", {}).get(zustand_consumer) or [])
            ),
            results.get("transitive_hook_consumers", {}).get(zustand_consumer),
        ),
        _check(
            "consumer_does_not_inherit_store_ownership",
            zustand_consumer not in (results.get("zustand_stores") or {}),
            results.get("zustand_stores", {}),
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {
            "kind": "react_transitive_propagation_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_react_transitive_propagation",
        },
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "react_transitive_propagation_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "react_transitive_propagation_validation.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# React Transitive Propagation Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []):
        result = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"| `{check.get('name')}` | {result} | `{check.get('details')}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
