from __future__ import annotations

from tools.engines.react_ecosystem_analyzer import analyze_react_ecosystem_file


def _risks(path: str, source: str) -> set[str]:
    row = analyze_react_ecosystem_file("FIXTURE", path, source)
    assert row is not None
    return {str(finding["risk"]) for finding in row["findings"]}


def test_control_ownership_accepts_boolean_shorthand_and_hidden_serialization() -> None:
    source = """
    export function Form({ name, token }) {
      return <form>
        <input value={name} disabled />
        <input value={name} readOnly />
        <input type="hidden" value={token} />
      </form>;
    }
    """
    assert "controlled_uncontrolled_input_contract_risk" not in _risks("src/Form.tsx", source)


def test_control_ownership_still_rejects_editable_control_without_owner() -> None:
    source = "export function Form({ name }) { return <input value={name} />; }"
    assert "controlled_uncontrolled_input_contract_risk" in _risks("src/Form.tsx", source)


def test_control_ownership_reads_complete_custom_control_opening_tags() -> None:
    managed = """
    export function Form({ option, setOption }) {
      return <>
        <Select value={options.find((candidate) => candidate.id === option)} onChange={setOption} />
        <Select value={option} onValueChange={(value) => setOption(value)} />
        <Select value={option} isDisabled={true} />
      </>;
    }
    """
    unmanaged = "export function Form({ option }) { return <Select value={option} />; }"
    assert "controlled_uncontrolled_input_contract_risk" not in _risks("src/ManagedSelect.tsx", managed)
    assert "controlled_uncontrolled_input_contract_risk" in _risks("src/UnmanagedSelect.tsx", unmanaged)


def test_query_contract_accepts_provider_managed_and_delegated_options() -> None:
    trpc = "import { trpc } from './trpc'; export function useUser(id) { return trpc.user.byId.useQuery({ id }); }"
    delegated = "import { useQuery } from '@tanstack/react-query'; export function useUser(id) { return useQuery(userOptions(id)); }"
    assert "trpc_query_cache_contract_risk" not in _risks("src/useTrpcUser.ts", trpc)
    assert "tanstack_query_cache_contract_risk" not in _risks("src/useQueryUser.ts", delegated)


def test_query_contract_still_rejects_direct_options_without_query_key() -> None:
    source = "import { useQuery } from '@tanstack/react-query'; export function useUser() { return useQuery({ queryFn: loadUser }); }"
    assert "tanstack_query_cache_contract_risk" in _risks("src/useUser.ts", source)


def test_query_contract_accepts_dynamic_key_value_but_not_comment_lookalike() -> None:
    dynamic = "import { useQuery } from '@tanstack/react-query'; export function useUser(id) { return useQuery({ queryKey: makeKey(id), queryFn: loadUser }); }"
    comment_only = "import { useQuery } from '@tanstack/react-query'; export function useUser() { return useQuery({ /* queryKey: fake */ queryFn: loadUser }); }"
    assert "tanstack_query_cache_contract_risk" not in _risks("src/useDynamicKey.ts", dynamic)
    assert "tanstack_query_cache_contract_risk" in _risks("src/useCommentKey.ts", comment_only)


def test_lazy_contract_ignores_schema_and_orm_member_methods() -> None:
    schema = "import { z } from 'zod'; export function Panel(){ const Node=z.lazy(() => z.object({})); return <div/>; }"
    orm = "import React from 'react'; export function Panel(){ const q=db.select().$dynamic(); return <div/>; }"
    assert "lazy_component_without_loading_or_error_boundary" not in _risks("src/SchemaPanel.tsx", schema)
    assert "lazy_component_without_loading_or_error_boundary" not in _risks("src/DataPanel.tsx", orm)


def test_lazy_contract_still_detects_react_or_next_dynamic_components() -> None:
    next_source = "import dynamic from 'next/dynamic'; const Editor = dynamic(() => import('./Editor')); export function Shell(){ return <Editor/>; }"
    combined_react_import = "import React, { lazy } from 'react'; const Editor = lazy(() => import('./Editor')); export function Shell(){ return <Editor/>; }"
    aliased_react_import = "import { lazy as loadComponent } from 'react'; const Editor = loadComponent(() => import('./Editor')); export function Shell(){ return <Editor/>; }"
    assert "lazy_component_without_loading_or_error_boundary" in _risks("src/NextShell.tsx", next_source)
    assert "lazy_component_without_loading_or_error_boundary" in _risks("src/ReactShell.tsx", combined_react_import)
    assert "lazy_component_without_loading_or_error_boundary" in _risks("src/AliasedReactShell.tsx", aliased_react_import)


def test_feature_contract_distinguishes_telemetry_from_feature_evaluation() -> None:
    telemetry = "import posthog from 'posthog-js'; export function Track(){ posthog.capture('open'); return <div/>; }"
    evaluation = "import posthog from 'posthog-js'; export function Panel(){ return posthog.isFeatureEnabled('new-ui') ? <New/> : <Old/>; }"
    assert "feature_flag_or_env_branch_needs_runtime_probe" not in _risks("src/Track.tsx", telemetry)
    assert "feature_flag_or_env_branch_needs_runtime_probe" in _risks("src/Panel.tsx", evaluation)


def test_feature_contract_ignores_comment_and_string_lookalikes() -> None:
    source = "export function Panel(){ const note='posthog.isFeatureEnabled(\\'x\\')'; /* useFeatureFlag('x') */ return <div/>; }"
    assert "feature_flag_or_env_branch_needs_runtime_probe" not in _risks("src/Panel.tsx", source)


def test_hydration_contract_applies_only_to_hydratable_production_surfaces() -> None:
    email = "import { Html } from '@react-email/components'; export function Notice(){ return <Html>{new Date().toString()}</Html>; }"
    test_source = "export function Clock(){ return <time>{Date.now()}</time>; }"
    production = "export function Clock(){ return <time>{Date.now()}</time>; }"
    assert "hydration_sensitive_render_value" not in _risks("packages/email/Notice.tsx", email)
    assert "hydration_sensitive_render_value" not in _risks("src/Clock.test.tsx", test_source)
    assert "hydration_sensitive_render_value" in _risks("src/Clock.tsx", production)


def test_state_owner_requires_duplication_evidence_not_state_kind_coexistence() -> None:
    coexistence = """
    export function Dashboard(){
      const query = useQuery({ queryKey: ['dashboard'], queryFn: loadDashboard });
      const [a] = useState(false); const [b] = useState(false); const [c] = useState(false);
      const [d] = useState(false); const [e] = useState(false); const [f] = useState(false);
      return <div>{query.data?.name}</div>;
    }
    """
    derived = "export function List({ items }) { const [visible] = useState(items.filter(Boolean)); return <div>{visible.length}</div>; }"
    assert "ambiguous_or_duplicated_state_owner" not in _risks("src/Dashboard.tsx", coexistence)
    assert "ambiguous_or_duplicated_state_owner" in _risks("src/List.tsx", derived)


def test_async_wrapper_accepts_mutation_owned_error_and_pending_contracts() -> None:
    source = """
    export function Feedback(){
      const mutation = trpc.feedback.useMutation({ onError: showError });
      return <Button loading={mutation.isPending} onClick={async () => { await mutation.mutate({ ok: true }); }}>Send</Button>;
    }
    """
    assert "async_user_event_without_error_or_pending_contract" not in _risks("src/Feedback.tsx", source)


def test_async_handler_without_error_or_pending_contract_remains_a_finding() -> None:
    source = "export function Create(){ return <button onClick={async () => { await createItem(); }}>Create</button>; }"
    assert "async_user_event_without_error_or_pending_contract" in _risks("src/Create.tsx", source)


def test_server_action_contract_requires_jsx_action_binding_not_lexical_name() -> None:
    lexical = "export function Email({ action = 'created' }) { return <div>{action}</div>; }"
    form_action = "export function Form(){ return <form action={save}><button>Save</button></form>; }"
    assert "async_or_expensive_interaction_without_concurrency_contract" not in _risks("src/Email.tsx", lexical)
    assert "async_or_expensive_interaction_without_concurrency_contract" in _risks("src/Form.tsx", form_action)


def test_non_react_typescript_action_and_comparison_do_not_enter_react_analysis() -> None:
    source = "export function handle(rule, left, right) { const action = rule.action; return left < right && right > 0 ? action : null; }"
    assert analyze_react_ecosystem_file("FIXTURE", "apps/api/CrmService.ts", source) is None


def test_list_contract_does_not_require_virtualization_from_list_count_alone() -> None:
    source = "export function Summary({teams, tags}) { return <><div>{teams.map(team => <span key={team.id}>{team.name}</span>)}</div><div>{tags.map(tag => <span key={tag}>{tag}</span>)}</div></>; }"
    assert "unstable_or_unbounded_list_render_contract" not in _risks("src/Summary.tsx", source)


def test_list_contract_ignores_static_document_renderer_reconciliation() -> None:
    source = "import { Document, Text } from '@react-pdf/renderer'; export function Invoice({rows}) { return <Document>{rows.map((row, index) => <Text key={index}>{row}</Text>)}</Document>; }"
    assert "unstable_or_unbounded_list_render_contract" not in _risks("src/Invoice.tsx", source)
