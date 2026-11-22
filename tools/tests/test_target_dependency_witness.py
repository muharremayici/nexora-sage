"""Finite loaded-graph navigation fixtures; no parser, target runner or corpus."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from tools.core import test_dependency_witness as witness
from tools.core import test_impact_profiles as profiles
from tools.core.agent_packet_budget import context_budget_profile
from tools.engines import test_impact_matcher as matcher
from tools.mcp import server


def inputs(*, extra_tests=0, project='MAIN', prefix='src/'):
    files = ['View.tsx', 'Screen.tsx', 'smoke.test.tsx']
    files += [f'other{i}.test.tsx' for i in range(extra_tests)]
    atlas = {project: {'root_path': prefix.rstrip('/') or '.', 'files': {
        path: {'workspace_rel': prefix + path} for path in files}}}
    graph = {'nodes': {project + '::' + path: {} for path in files}, 'edges': [
        {'source': project + '::smoke.test.tsx', 'target': project + '::Screen.tsx'},
        {'source': project + '::Screen.tsx', 'target': project + '::View.tsx'}]}
    graph['edges'] += [{'source': project + f'::other{i}.test.tsx', 'target': project + '::View.tsx'}
                      for i in range(extra_tests)]
    return atlas, graph


def result(atlas=None, graph=None, *, target='MAIN::src/View.tsx'):
    if atlas is None:
        atlas, graph = inputs()
    return matcher.find_impacted_tests(target, atlas=atlas, circular_deps=graph)


def view(payload, index=0):
    return payload['impacted_tests'][index]['dependency_witness']


def no_new_detail(payload):
    payload = deepcopy(payload)
    payload.pop('dependency_navigation', None)
    for row in payload['impacted_tests']:
        row.pop('dependency_witness', None)
    return payload


@pytest.mark.parametrize('project', ['MAIN', 'Prójé'])
@pytest.mark.parametrize('prefix', ['src/', 'packages/ui/'])
def test_complete_forward_dependency_path_and_public_refs(project, prefix):
    atlas, graph = inputs(project=project, prefix=prefix)
    before = deepcopy((atlas, graph))
    payload = result(atlas, graph, target=project + '::' + prefix + 'View.tsx')
    nav = view(payload)
    assert nav['status'] == 'observed'
    assert nav['nodes'] == [project + '::' + path for path in ['smoke.test.tsx', 'Screen.tsx', 'View.tsx']]
    assert nav['target_refs'] == [project + '::' + prefix + path for path in ['smoke.test.tsx', 'Screen.tsx', 'View.tsx']]
    assert nav['runtime_execution'] == nav['changed_behavior'] == 'not_established'
    assert nav['mutation_authority'] == 'none'
    assert witness.project_dependency_witness(payload['impacted_tests'][0], payload) == nav
    assert (atlas, graph) == before


def test_direct_and_dual_vector_relations_do_not_gain_behavioral_coverage():
    atlas, graph = inputs()
    atlas['MAIN']['files']['View.test.tsx'] = {'workspace_rel': 'src/View.test.tsx'}
    graph['nodes']['MAIN::View.test.tsx'] = {}
    graph['edges'].append({'source': 'MAIN::View.test.tsx', 'target': 'MAIN::View.tsx'})
    payload = result(atlas, graph)
    row = payload['impacted_tests'][0]
    assert row['type'] == 'Dual Vector Match' and row['static_relation'] == 'direct_import'
    assert row['confidence'] == 1.0
    assert row['candidate_evidence']['changed_behavior'] == 'not_established'
    assert row['dependency_witness']['nodes'] == ['MAIN::View.test.tsx', 'MAIN::View.tsx']


def test_naming_only_candidate_cannot_invent_graph_path():
    atlas, graph = inputs()
    atlas['MAIN']['files']['View.test.tsx'] = {'workspace_rel': 'src/View.test.tsx'}
    payload = result(atlas, graph)
    row = next(r for r in payload['impacted_tests'] if r['file'] == 'View.test.tsx')
    assert row['candidate_evidence']['relation'] == 'naming_only'
    assert row['dependency_witness']['reason'] == 'no_static_dependency_relation'


def test_shared_dependency_fork_is_not_test_to_provider_path():
    atlas, graph = inputs()
    graph['edges'] = [
        {'source': 'MAIN::Screen.tsx', 'target': 'MAIN::View.tsx'},
        {'source': 'MAIN::smoke.test.tsx', 'target': 'MAIN::View.tsx'}]
    assert result(atlas, graph, target='MAIN::src/Screen.tsx')['impacted_tests'] == []


def test_cycle_duplicate_and_edge_order_keep_one_deterministic_simple_path():
    atlas, graph = inputs()
    atlas['MAIN']['files']['Another.tsx'] = {'workspace_rel': 'src/Another.tsx'}
    graph['nodes']['MAIN::Another.tsx'] = {}
    graph['edges'] += [
        {'source': 'MAIN::Another.tsx', 'target': 'MAIN::View.tsx'},
        {'source': 'MAIN::smoke.test.tsx', 'target': 'MAIN::Another.tsx'},
        {'source': 'MAIN::View.tsx', 'target': 'MAIN::Screen.tsx'},
        deepcopy(graph['edges'][0])]
    first = result(atlas, graph)
    graph['edges'].reverse()
    second = result(atlas, graph)
    assert view(first) == view(second)
    assert view(first)['nodes'] == ['MAIN::smoke.test.tsx', 'MAIN::Another.tsx', 'MAIN::View.tsx']
    assert len(set(view(first)['nodes'])) == len(view(first)['nodes'])


@pytest.mark.parametrize('change', ['max_trace_nodes', 'max_path_nodes', 'max_candidates', 'max_group_bytes', 'max_reference_characters'])
def test_optional_retention_limits_never_change_candidates_or_rank(monkeypatch, change):
    atlas, graph = inputs(extra_tests=2)
    baseline = result(atlas, graph)
    profile = deepcopy(profiles.load_test_impact_profiles())
    profile['dependency_witness'][change] = 2 if change == 'max_path_nodes' else 1
    monkeypatch.setattr(profiles, 'load_test_impact_profiles', lambda: profile)
    bounded = result(atlas, graph)
    assert no_new_detail(bounded) == no_new_detail(baseline)
    assert (bounded['dependency_navigation']['omitted_candidates'] > 0
            or any(r.get('dependency_witness', {}).get('status') == 'unavailable' for r in bounded['impacted_tests']))
    assert all(r['candidate_evidence']['test_execution'] == 'not_run_by_sage' for r in bounded['impacted_tests'])


@pytest.mark.parametrize('change', ['missing', 'unknown_version', 'bool_limit', 'zero', 'extra'])
def test_bad_optional_policy_withholds_navigation_not_ordinary_matching(monkeypatch, change):
    atlas, graph = inputs()
    baseline = result(atlas, graph)
    profile = deepcopy(profiles.load_test_impact_profiles())
    policy = profile['dependency_witness']
    if change == 'missing': profile.pop('dependency_witness')
    elif change == 'unknown_version': policy['version'] = 'unknown'
    elif change == 'bool_limit': policy['max_candidates'] = True
    elif change == 'zero': policy['max_trace_nodes'] = 0
    else: policy['skip_tests'] = True
    monkeypatch.setattr(profiles, 'load_test_impact_profiles', lambda: profile)
    actual = result(atlas, graph)
    assert no_new_detail(actual) == no_new_detail(baseline)
    assert actual['dependency_navigation']['reason'] == 'optional_policy_unavailable'
    assert all('dependency_witness' not in row for row in actual['impacted_tests'])


@pytest.mark.parametrize('change', ['missing_node', 'missing_atlas_file', 'ambiguous_public', 'missing_workspace', 'traversal'])
def test_unavailable_path_identity_is_not_borrowed_or_repaired(change):
    atlas, graph = inputs()
    if change == 'missing_node': graph['nodes'].pop('MAIN::Screen.tsx')
    elif change == 'missing_atlas_file': atlas['MAIN']['files'].pop('Screen.tsx')
    elif change == 'ambiguous_public': atlas['MAIN']['files']['Other.tsx'] = {'workspace_rel': 'src/Screen.tsx'}
    elif change == 'missing_workspace': atlas['MAIN']['files']['Screen.tsx'] = {}
    else: atlas['MAIN']['files']['Screen.tsx']['workspace_rel'] = '../Screen.tsx'
    actual = result(atlas, graph)
    if change == 'missing_node':
        assert actual['impacted_tests'] == []
    else:
        assert view(actual)['status'] == 'unavailable'


def test_cross_project_same_filename_chain_keeps_each_own_identity():
    atlas, graph = inputs()
    atlas['LIB'] = {'root_path': 'lib', 'files': {'View.tsx': {'workspace_rel': 'lib/View.tsx'}}}
    graph['nodes']['LIB::View.tsx'] = {}
    graph['edges'].append({'source': 'MAIN::View.tsx', 'target': 'LIB::View.tsx'})
    payload = result(atlas, graph, target='LIB::lib/View.tsx')
    assert view(payload)['nodes'][-2:] == ['MAIN::View.tsx', 'LIB::View.tsx']
    assert view(payload)['target_refs'][-2:] == ['MAIN::src/View.tsx', 'LIB::lib/View.tsx']


@pytest.mark.parametrize('change', ['policy', 'version', 'runtime', 'authority', 'unknown_field', 'reverse', 'cycle', 'target', 'public', 'project', 'relation', 'live'])
def test_carrier_or_endpoint_mismatch_cannot_survive_public_normalization(change):
    payload = result()
    row, nav = payload['impacted_tests'][0], view(payload)
    if change == 'policy': nav['policy_sha256'] = '0' * 64
    elif change == 'version': nav['version'] = 'legacy'
    elif change == 'runtime': nav['runtime_execution'] = 'confirmed'
    elif change == 'authority': nav['mutation_authority'] = 'granted'
    elif change == 'unknown_field': nav['safe_to_apply'] = True
    elif change == 'reverse': nav['nodes'].reverse()
    elif change == 'cycle': nav['nodes'][1] = nav['nodes'][0]
    elif change == 'target': payload['target'] = 'MAIN::Other.tsx'
    elif change == 'public': row['repo_relative_path'] = 'src/other.test.tsx'
    elif change == 'project': row['project'] = 'OTHER'
    elif change == 'relation': row['static_relation'] = 'direct_import'
    else: row['source'] = 'live_bounded_sibling_scan'
    assert server._normalize_test_impact_payload_for_agent(payload)['impacted_tests'][0]['dependency_witness']['status'] == 'unavailable'


def test_live_direct_promotion_cannot_borrow_old_transitive_graph_chain(monkeypatch, tmp_path):
    payload = result()
    monkeypatch.setattr(server, '_direct_colocated_test_candidates', lambda *_: [{
        'file': 'src/smoke.test.tsx', 'repo_relative_path': 'src/smoke.test.tsx',
        'type': 'Direct Co-located Static Import', 'confidence': 1.0,
        'source': 'live_bounded_sibling_scan', 'run_command': 'unchanged'}])
    merged = server._merge_live_direct_test_candidates(payload, tmp_path, 'src/View.tsx')
    normalized = server._normalize_test_impact_payload_for_agent(merged)
    assert normalized['impacted_tests'][0]['static_relation'] == 'direct_import'
    assert view(normalized)['status'] == 'unavailable'


@pytest.mark.parametrize('external', [False, True])
def test_actual_public_sqlite_default_external_machine_and_brief(external, tmp_path, monkeypatch):
    from tools.tests.test_target_test_impact_direction import _component_render_ref_public_sample
    sample = _component_render_ref_public_sample(tmp_path, monkeypatch, external, 'MAIN::src/View.tsx')
    payload, brief = sample['machine'], sample['brief']
    assert view(payload)['status'] == 'observed'
    assert view(payload)['target_refs'] == ['MAIN::src/smoke.test.tsx', 'MAIN::src/Screen.tsx', 'MAIN::src/View.tsx']
    assert 'Dependency navigation (not executed):' in brief
    assert 'not_run_by_sage' in brief and 'not_established' in brief
    assert context_budget_profile(brief, budget_tokens=server.BOUNDED_AGENT_PACKET_TOKENS)['status'] == 'pass'


def test_brief_preserves_complete_older_output_and_command_details():
    payload = result()
    row = payload['impacted_tests'][0]
    row['source_snippets'] = [{'code': 'expect(View).toBeDefined()', 'matched_line': 1, 'source_lines': '1-1'}]
    row['package_script_suggestion'] = {'status': 'unavailable', 'execution': 'not_run_by_sage'}
    before = deepcopy(payload)
    with patch.object(server, '_append_test_dependency_witnesses', side_effect=lambda body, *_: body):
        baseline = server._render_test_impact_brief(payload)
    body = server._render_test_impact_brief(payload)
    assert body.startswith(baseline)
    assert 'Command source (not executed):' in baseline
    assert 'Dependency navigation (not executed):' in body
    assert payload == before


@pytest.mark.parametrize('budget', [600, 1100, 3000])
@pytest.mark.parametrize('prefix', ['src/', '/'.join(['long_' + '界' * 50] * 4) + '/'])
def test_optional_paths_never_evict_visible_candidates_or_exceed_budget(monkeypatch, budget, prefix):
    atlas, graph = inputs(extra_tests=5, prefix=prefix)
    payload = result(atlas, graph, target='MAIN::' + prefix + 'View.tsx')
    monkeypatch.setattr(server, 'BOUNDED_AGENT_PACKET_TOKENS', budget)
    with patch.object(server, '_append_test_dependency_witnesses', side_effect=lambda body, *_: body):
        baseline = server._render_test_impact_brief(payload)
    body = server._render_test_impact_brief(payload)
    assert body.startswith(baseline)
    assert context_budget_profile(body, budget_tokens=budget)['status'] == 'pass'
    if 'Dependency navigation (not executed):' in body:
        summary = json.loads(body.split('Dependency navigation (not executed): ', 1)[1])
        assert summary['shown_candidates'] + summary['omitted_candidates'] == len(payload['impacted_tests'])
        assert 'does not prove absent relevant tests' in summary['proof_boundary']
        for detail in summary['items']:
            if detail['status'] == 'observed':
                assert detail['dependency_path'][-1] == payload['target_ref']


def test_explicit_graph_input_does_not_read_host_or_target(monkeypatch):
    monkeypatch.setattr(matcher, 'load_atlas_data', lambda: pytest.fail('host Atlas read'))
    monkeypatch.setattr(matcher, 'load_json_file', lambda *_: pytest.fail('host graph read'))
    assert view(result())['status'] == 'observed'


def test_legacy_payload_has_no_synthesized_observed_witness():
    payload = result()
    payload['impacted_tests'][0].pop('dependency_witness')
    normalized = server._normalize_test_impact_payload_for_agent(payload)
    assert 'dependency_witness' not in normalized['impacted_tests'][0]
    assert 'Dependency navigation (not executed):' not in server._render_test_impact_brief(normalized)


def test_public_projection_counts_current_paths_after_live_promotion():
    payload = result()
    payload['impacted_tests'][0]['source'] = 'live_bounded_sibling_scan'
    normalized = server._normalize_test_impact_payload_for_agent(payload)
    assert view(normalized)['status'] == 'unavailable'
    assert normalized['dependency_navigation']['observed_paths'] == 0


def test_public_projection_does_not_borrow_forged_retention_summary():
    payload = result()
    payload['dependency_navigation'] = {'observed_paths': 99, 'mutation_authority': 'granted'}
    normalized = server._normalize_test_impact_payload_for_agent(payload)
    assert normalized['dependency_navigation']['observed_paths'] == 1
    assert 'mutation_authority' not in normalized['dependency_navigation']


@pytest.mark.parametrize('position', ['node', 'reference'])
def test_unencodable_navigation_identity_is_unavailable_not_candidate_failure(position):
    payload = result()
    field = 'nodes' if position == 'node' else 'target_refs'
    view(payload)[field][1] = 'MAIN::bad\ud800.tsx'
    normalized = server._normalize_test_impact_payload_for_agent(payload)
    assert len(normalized['impacted_tests']) == 1
    assert view(normalized)['status'] == 'unavailable'


def test_machine_navigation_retention_is_bounded_without_dropping_candidates():
    atlas, graph = inputs(extra_tests=12)
    payload = result(atlas, graph)
    assert len(payload['impacted_tests']) == 13
    summary = payload['dependency_navigation']
    assert summary['retained_candidates'] == 8 and summary['omitted_candidates'] == 5
    assert sum('dependency_witness' in row for row in payload['impacted_tests']) == 8
    normalized = server._normalize_test_impact_payload_for_agent(payload)
    assert normalized['dependency_navigation']['retained_candidates'] == 8
    assert normalized['dependency_navigation']['omitted_candidates'] == 5


def test_retained_direct_path_remains_complete_when_trace_is_partial(monkeypatch):
    atlas, graph = inputs()
    graph['edges'] = []  # This control selects only two direct dependents for the trace cap.
    for name in ('a.test.tsx', 'z.test.tsx'):
        atlas['MAIN']['files'][name] = {'workspace_rel': 'src/' + name}
        graph['nodes']['MAIN::' + name] = {}
        graph['edges'].append({'source': 'MAIN::' + name, 'target': 'MAIN::View.tsx'})
    policy = deepcopy(profiles.load_test_impact_profiles())
    policy['dependency_witness']['max_trace_nodes'] = 2
    monkeypatch.setattr(profiles, 'load_test_impact_profiles', lambda: policy)
    payload = result(atlas, graph)
    row = next(row for row in payload['impacted_tests'] if row['file'] == 'a.test.tsx')
    assert row['dependency_witness']['nodes'] == ['MAIN::a.test.tsx', 'MAIN::View.tsx']
    assert row['dependency_witness']['trace_retention'] == 'partial'
    assert any(r.get('dependency_witness', {}).get('status') == 'unavailable' for r in payload['impacted_tests'])
