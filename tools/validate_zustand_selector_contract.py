from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.subprocess_telemetry import run_observed_subprocess


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_ast_fixture(source: str, filename: str) -> set[str]:
    with tempfile.TemporaryDirectory(prefix="nexora-zustand-") as tmp:
        fixture = Path(tmp) / filename
        fixture.write_text(source, encoding="utf-8")
        from tools.core.artifact_store import get_adaptive_timeout
        result, _duration = run_observed_subprocess(
            ["node", str(CODE_MAPS_DIR / "tools" / "engines" / "ast_sequencer.cjs"), str(fixture)],
            cwd=CODE_MAPS_DIR,
            label=f"zustand_selector_fixture_{filename}",
            timeout=get_adaptive_timeout(180),
            log=print,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or "ast_sequencer.cjs failed")
        payload = json.loads(result.stdout)
    if not isinstance(payload, list):
        raise RuntimeError("ast_sequencer.cjs returned a non-list payload")
    meta = next((item for item in payload if isinstance(item, dict) and item.get("name") == "__file_meta__"), {})
    return set(meta.get("features") or [])


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def run_validation() -> dict[str, Any]:
    no_selector_features = _run_ast_fixture(
        """
        import { useProjectStore } from '@/stores/projectStore';
        export function ProjectPanel() {
          const store = useProjectStore();
          return <section>{store.project?.title}</section>;
        }
        """,
        "ProjectPanel.tsx",
    )
    selector_features = _run_ast_fixture(
        """
        import { useProjectStore } from '@/stores/projectStore';
        export function ProjectTitle() {
          const title = useProjectStore(s => s.project.title);
          return <h1>{title}</h1>;
        }
        """,
        "ProjectTitle.tsx",
    )
    create_features = _run_ast_fixture(
        """
        import { create } from 'zustand';
        export const useProjectStore = create((set) => ({
          project: null,
          setProject: (project) => set({ project }),
        }));
        """,
        "projectStore.ts",
    )
    create_get_features = _run_ast_fixture(
        """
        import { create } from 'zustand';
        export const useProjectStore = create((set, get) => ({
          project: null,
          setProject: (project) => set({ project }),
          currentTitle: () => get().project?.title,
        }));
        """,
        "projectStoreWithGet.ts",
    )
    suffixless_features = _run_ast_fixture(
        """
        import { useAuthor } from '@/stores/authorStore';
        export function AuthorPanel() {
          const author = useAuthor();
          return <section>{author.name}</section>;
        }
        """,
        "AuthorPanel.tsx",
    )
    broad_selector_features = _run_ast_fixture(
        """
        import { useAuthorStore } from '@/stores/authorStore';
        export function AuthorPanel() {
          const author = useAuthorStore(s => s);
          return <section>{author.name}</section>;
        }
        """,
        "AuthorWholeStorePanel.tsx",
    )
    broad_selector_named_features = _run_ast_fixture(
        """
        import { useAuthorStore } from '@/stores/authorStore';
        export function AuthorPanel() {
          const author = useAuthorStore(state => state);
          return <section>{author.name}</section>;
        }
        """,
        "AuthorWholeStoreNamedParamPanel.tsx",
    )
    spread_selector_features = _run_ast_fixture(
        """
        import { useAuthorStore } from '@/stores/authorStore';
        export function AuthorPanel() {
          const author = useAuthorStore(s => ({ ...s }));
          return <section>{author.name}</section>;
        }
        """,
        "AuthorSpreadStorePanel.tsx",
    )
    object_assign_selector_features = _run_ast_fixture(
        """
        import { useAuthorStore } from '@/stores/authorStore';
        export function AuthorPanel() {
          const author = useAuthorStore(s => Object.assign({}, s));
          return <section>{author.name}</section>;
        }
        """,
        "AuthorObjectAssignStorePanel.tsx",
    )
    shallow_wrapper_features = _run_ast_fixture(
        """
        import { useShallow } from 'zustand/react/shallow';
        import { useAuthorStore } from '@/stores/authorStore';
        export function AuthorPanel() {
          const author = useAuthorStore(useShallow(s => ({ name: s.name })));
          return <section>{author.name}</section>;
        }
        """,
        "AuthorShallowSelectorPanel.tsx",
    )
    query_factory_features = _run_ast_fixture(
        """
        import { useQuery } from '@tanstack/react-query';
        import { queryKeys } from './queryKeys';
        export function useAuthor(id) {
          return useQuery({ queryKey: queryKeys.author.details(id), queryFn: () => fetch('/api/authors/' + id) });
        }
        """,
        "useAuthorQuery.tsx",
    )
    static_literal_query_features = _run_ast_fixture(
        """
        import { useQuery } from '@tanstack/react-query';
        export function Posts() {
          return useQuery({ queryKey: ['posts', 10, true, null], queryFn: () => fetch('/api/posts') });
        }
        """,
        "StaticLiteralQuery.tsx",
    )
    scoped_form_features = _run_ast_fixture(
        """
        import { useForm } from 'react-hook-form';
        export function ProfileForm() {
          const form = useForm();
          const { register, handleSubmit } = useForm();
          return <form onSubmit={form.handleSubmit(() => {})}>
            <input {...form.register('email')} />
            <input {...register('name')} />
            <button onClick={handleSubmit(() => {})}>Save</button>
          </form>;
        }
        """,
        "ProfileForm.tsx",
    )
    register_false_positive_features = _run_ast_fixture(
        """
        import { Chart } from 'chart.js';
        Chart.register();
        export function chartPlugin() {
          return {
            register() {},
            handleSubmit() {},
          };
        }
        """,
        "ChartPlugin.ts",
    )

    checks = [
        _check(
            "zustand_hook_without_selector_is_tagged",
            "Zustand:NoSelector" in no_selector_features,
            sorted(no_selector_features),
        ),
        _check(
            "zustand_hook_with_selector_is_not_tagged",
            "Zustand:NoSelector" not in selector_features and "Tech:selector" in selector_features,
            sorted(selector_features),
        ),
        _check(
            "zustand_create_store_is_not_false_positive",
            "Zustand:NoSelector" not in create_features and "ZustandStore" in create_features,
            sorted(create_features),
        ),
        _check(
            "zustand_create_store_exposes_transition_evidence",
            "ZustandStore" in create_get_features
            and "Tech:setState" in create_get_features
            and "Tech:getState" in create_get_features,
            sorted(create_get_features),
        ),
        _check(
            "suffixless_store_hook_from_store_path_is_tagged",
            "Zustand:NoSelector" in suffixless_features,
            sorted(suffixless_features),
        ),
        _check(
            "whole_store_selector_is_broad_selector",
            "Zustand:BroadSelector" in broad_selector_features
            and "Zustand:BroadSelector:useAuthorStore" in broad_selector_features,
            sorted(broad_selector_features),
        ),
        _check(
            "whole_store_selector_named_param_is_broad_selector",
            "Zustand:BroadSelector" in broad_selector_named_features,
            sorted(broad_selector_named_features),
        ),
        _check(
            "spread_store_selector_is_broad_selector",
            "Zustand:BroadSelector" in spread_selector_features,
            sorted(spread_selector_features),
        ),
        _check(
            "object_assign_store_selector_is_broad_selector",
            "Zustand:BroadSelector" in object_assign_selector_features,
            sorted(object_assign_selector_features),
        ),
        _check(
            "zustand_use_shallow_helper_is_not_store_consumer",
            "Tech:selector:useShallow" not in shallow_wrapper_features
            and "Zustand:NoSelector:useShallow" not in shallow_wrapper_features,
            sorted(shallow_wrapper_features),
        ),
        _check(
            "computed_tanstack_query_key_is_preserved_as_dynamic_expression",
            "QueryKeyDynamic:queryKeys.author.details(id)" in query_factory_features,
            sorted(query_factory_features),
        ),
        _check(
            "static_tanstack_query_key_literals_are_not_dynamic",
            "QueryKey:posts" in static_literal_query_features
            and "QueryKey:10" in static_literal_query_features
            and "QueryKey:true" in static_literal_query_features
            and "QueryKey:null" in static_literal_query_features
            and "QueryKeyDynamic:10" not in static_literal_query_features,
            sorted(static_literal_query_features),
        ),
        _check(
            "react_hook_form_methods_are_scoped_to_use_form_bindings",
            "ReactForm" in scoped_form_features
            and "FormFieldRegister" in scoped_form_features
            and "FormSubmitHandler" in scoped_form_features,
            sorted(scoped_form_features),
        ),
        _check(
            "arbitrary_register_methods_do_not_create_react_form_evidence",
            "ReactForm" not in register_false_positive_features
            and "FormFieldRegister" not in register_false_positive_features
            and "FormSubmitHandler" not in register_false_positive_features,
            sorted(register_false_positive_features),
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {
            "kind": "zustand_selector_contract_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_zustand_selector_contract",
        },
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "zustand_selector_contract_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "zustand_selector_contract_validation.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Zustand Selector Contract Validation",
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
