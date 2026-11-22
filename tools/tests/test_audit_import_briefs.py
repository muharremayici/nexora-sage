"""Presentation seam fixtures: no AST, Atlas acquisition, target commands or corpus."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.core import audit_finding_generation as evidence
from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION
from tools.core.import_occurrence_evidence import project_import_occurrences, occurrence_policy
from tools.core.agent_packet_budget import context_budget_profile, estimate_tokens
from tools.mcp import server


def carrier_item(*, count=3, path='domain/Panel.ts'):
    """A controlled already-verified carrier at the consumer seam, not new source proof."""
    digest = hashlib.sha256(b'controlled captured-source fixture').hexdigest()
    meta = {'language': 'typescript', 'hash': digest, 'ast_contract_version': AST_CONTRACT_VERSION,
        'parser_evidence': {'status': 'observed'}}
    record = {'source': 'widgets/View.ts', 'raw_source': '@/widgets/View',
        'name': 'View', 'kind': 'named', 'scope': 'top_level'}
    base = evidence.build_audit_import_subject(project='MAIN', rel_path=path,
        file_data=meta, snapshot_id='controlled-snapshot-1',
        source_binding={'status': 'ok', 'source_hash': digest},
        policy={'identity': 'controlled-policy-fixture', 'atlas_snapshot_id': 'controlled-snapshot-1'},
        import_record=record, source_layer={'pointer': '/fixture/domain'},
        target_layer={'pointer': '/fixture/widgets'}, mapping={'violated': True, 'pointer': '/fixture/mapping'})
    assert base['status'] == 'bound_record_set'
    def span(start, end, line): return {'start': start, 'end': end, 'line': line, 'end_line': line}
    rows = []
    for i in range(count):
        start, line = 10 + i * 100, i + 2
        rows.append({'record': {k: record['raw_source' if k == 'source' else k] for k in ('source', 'name', 'kind', 'scope')},
            'statement': span(start, start + 60, line), 'keyword': span(start, start + 6, line),
            'binding': span(start + 9, start + 13, line), 'name': span(start + 9, start + 13, line),
            'module': span(start + 40, start + 57, line)})
    policy = occurrence_policy(evidence.audit_import_subject_contract())
    stored = {'rows': rows, 'source_hash': digest, 'omitted': 0,
        'ast_contract_version': AST_CONTRACT_VERSION, 'policy_sha256': evidence._import_evidence_digest(policy)}
    view = project_import_occurrences(base, stored, policy)
    return {'id': 'fixture_item', 'target_project': 'MAIN', 'target_ref': 'MAIN::src/' + path,
        'target_file': 'src/' + path, 'file': 'src/' + path,
        'atlas_node': 'MAIN::' + path, 'path_projection_status': 'resolved',
        'rule': 'hexagonal_layer_violation', 'evidence': 'Captured import requires review.',
        'why_it_matters': 'Keep layer responsibilities separate.', 'fix_strategy': 'Inspect before editing.',
        'human_approval_required': True, 'approval_reason': 'Explicit approval remains required.',
        'inspect_first': ['src/' + path], 'finding_evidence': base, 'import_occurrence_evidence': view}


def payload(items):
    return {'analysis_root': 'C:/controlled-target', 'filter': 'MAIN::domain/Panel.ts',
        'artifact_trust': {'status': 'PASS'}, 'total_violations': len(items), 'items': items, 'status': 'debt_present'}


def navigation(body):
    lines = [line for line in body.splitlines() if line.startswith('captured_import_navigation: ')]
    assert len(lines) <= 1
    return json.loads(lines[0].split(': ', 1)[1]) if lines else None


def mandatory(body):
    """Compare preserved original text; no new YAML parser/test dependency."""
    return '\n'.join(line for line in body.split('\ncontext_budget:', 1)[0].splitlines()
        if not line.startswith('captured_import_navigation: '))


def summary(item, policy=None):
    return evidence.audit_import_location_brief([item], policy or evidence.audit_import_location_brief_policy())['items'][0]


@pytest.mark.parametrize('renderer', [server._render_violation_work_queue_brief, server._render_module_integrity_brief])
def test_default_briefs_show_bounded_snapshot_locations_without_replacing_mandatory_body(renderer):
    item = carrier_item(); data = payload([item]); before = deepcopy(data)
    with patch.object(evidence, 'audit_import_location_brief_policy', side_effect=ValueError('optional policy unavailable')):
        old_body = renderer(data)
    body = renderer(data); nav = navigation(body)
    assert mandatory(body) == mandatory(old_body) and data == before
    assert nav['scope'] == 'captured_source_snapshot_navigation_only'
    assert nav['offset_unit'] == 'utf8_bytes' and nav['line_numbering'] == 'one_based'
    assert nav['byte_ranges'] == 'start_inclusive_end_exclusive'
    assert nav['mutation_authority'] == 'none' and nav['live_source_currentness'] == 'not_evaluated'
    row = nav['items'][0]
    assert row['snapshot_id'] == item['finding_evidence']['atlas_snapshot_id']
    assert row['status'] == 'partial' and row['brief_locations_omitted'] == 1
    assert row['locations'] == [{'statement_lines': [2, 2], 'statement_bytes': [10, 70], 'binding_bytes': [19, 23]},
        {'statement_lines': [3, 3], 'statement_bytes': [110, 170], 'binding_bytes': [119, 123]}]
    assert 'controlled-policy-fixture' not in body and item['finding_evidence']['subjects'][0]['id'] not in body
    assert 'Explicit approval remains required.' in body if renderer is server._render_violation_work_queue_brief else 'Do not add suppressions' in body


@pytest.mark.parametrize('case', ['no_carrier', 'unavailable', 'base_digest', 'snapshot', 'source', 'project',
    'file', 'item', 'unresolved', 'version', 'policy', 'unit', 'ast', 'authority', 'runtime', 'unknown_subject',
    'bool_line', 'line_order', 'byte_order', 'outside', 'duplicate', 'missing_role', 'omitted_bool',
    'bound_with_omission', 'contradictory_missing', 'base_bool', 'reverse', 'line_outside',
    'item_public_ref', 'item_project', 'item_file', 'item_display_file'])
def test_malformed_or_cross_bound_metadata_withholds_only_optional_locations(case):
    item = carrier_item(); view, base = item['import_occurrence_evidence'], item['finding_evidence']
    if case == 'no_carrier': del item['import_occurrence_evidence']
    elif case == 'unavailable': view.update(status='unavailable', reason='source_not_available')
    elif case == 'base_digest': base['source_hash'] = 'f' * 64
    elif case in ('snapshot', 'source', 'project', 'file'):
        view[{'snapshot': 'atlas_snapshot_id', 'source': 'source_hash', 'project': 'project_key', 'file': 'atlas_rel_path'}[case]] = 'different'
    elif case == 'item': item['atlas_node'] = 'OTHER::domain/Panel.ts'
    elif case == 'unresolved': item['path_projection_status'] = 'unresolved'
    elif case == 'version': view['version'] = 'unknown'
    elif case == 'policy': view['policy_sha256'] = 'f' * 64
    elif case == 'unit': view['offset_unit'] = 'utf16_code_units'
    elif case == 'ast': view['ast_contract_version'] = 'old'
    elif case == 'authority': view['mutation_authority'] = 'approved'
    elif case == 'runtime': view['runtime_proof'] = 'passed'
    elif case == 'unknown_subject': view['rows'][0]['subject_id'] = 'a' * 64
    elif case == 'bool_line': view['rows'][0]['statement']['line'] = True
    elif case == 'line_order': view['rows'][0]['statement']['end_line'] = 1
    elif case == 'byte_order': view['rows'][0]['statement']['end'] = 0
    elif case == 'outside': view['rows'][0]['binding']['end'] = 1000
    elif case == 'duplicate': view['rows'][1] = deepcopy(view['rows'][0])
    elif case == 'missing_role': del view['rows'][0]['keyword']
    elif case == 'omitted_bool': view['producer_omitted'] = True
    elif case == 'bound_with_omission': view['producer_omitted'] = 1
    elif case == 'contradictory_missing': view['subjects_without_locations'] = [base['subjects'][0]['id']]
    elif case == 'reverse': view['rows'].reverse()
    elif case == 'line_outside': view['rows'][0]['binding'].update(line=99, end_line=99)
    elif case == 'item_public_ref': item['target_ref'] = 'OTHER::src/domain/Panel.ts'
    elif case == 'item_project': item['target_project'] = 'OTHER'
    elif case == 'item_file': item['target_file'] = 'src/other.ts'
    elif case == 'item_display_file': item['file'] = 'src/other.ts'
    else:
        base['omitted_subject_count'] = True
        base['binding_id'] = evidence._import_evidence_digest({k: v for k, v in base.items() if k != 'binding_id'})
        view['finding_binding_id'] = base['binding_id']
    before = deepcopy(item)
    result = summary(item)
    assert result['status'] == 'unavailable' and result['locations'] == []
    assert item == before
    body = server._render_violation_work_queue_brief(payload([item]))
    assert 'id: "fixture_item"' in body and 'total_violations: 1' in body


def test_partial_producer_carrier_subject_and_brief_omissions_stay_separate():
    item = carrier_item(); base, view = item['finding_evidence'], item['import_occurrence_evidence']
    base.update(status='partial', omitted_subject_count=4)
    base['binding_id'] = evidence._import_evidence_digest({k: v for k, v in base.items() if k != 'binding_id'})
    view.update(status='partial', finding_binding_id=base['binding_id'], producer_omitted=2, display_omitted=5, subject_omitted=4)
    result = summary(item)
    assert result['status'] == 'partial'
    assert (result['producer_omitted'], result['carrier_display_omitted'], result['base_subject_omitted'], result['brief_locations_omitted']) == (2, 5, 4, 1)


@pytest.mark.parametrize('different_snapshot', [False, True])
def test_grouped_findings_do_not_borrow_first_rows_locations(different_snapshot):
    first, second = carrier_item(), carrier_item()
    second['id'] = 'second_finding'; second['evidence'] = 'Separate evidence.'
    if different_snapshot: second['import_occurrence_evidence']['atlas_snapshot_id'] = 'other-snapshot'
    data = payload([first, second]); before = deepcopy(data)
    body = server._render_violation_work_queue_brief(data)
    assert 'grouped_count: 2' in body
    nav = navigation(body)['items'][0]
    assert nav['status'] == 'unavailable' and not nav['locations']
    assert nav['reason'] == 'grouped_findings_require_per_finding_machine_detail'
    assert 'snapshot_id' not in nav and data == before


def test_multiple_bindings_in_one_statement_remain_distinct_and_bounded():
    item = carrier_item(count=2); rows = item['import_occurrence_evidence']['rows']
    rows[1] = deepcopy(rows[0]); rows[1]['binding'] = {**rows[0]['binding'], 'start': 28, 'end': 32}
    rows[1]['name'] = deepcopy(rows[1]['binding'])
    result = summary(item)
    assert len(result['locations']) == 2 and result['status'] == 'available'
    assert result['locations'][0]['statement_bytes'] == result['locations'][1]['statement_bytes']
    assert result['locations'][0]['binding_bytes'] != result['locations'][1]['binding_bytes']


@pytest.mark.parametrize('headroom,expected', [(100, 'omitted_context_budget'), (1, None)])
def test_optional_budget_never_evicts_existing_text_or_truncates_qualifiers(headroom, expected):
    lines = ['mission: "Keep all safety instructions 🔎"', 'mandatory: "' + 'x' * 500 + '"']
    original = server._render_audit_brief_with_import_locations('# Fixture', lines, [], budget_tokens=5000)
    budget = estimate_tokens(original) + headroom
    baseline = server._render_audit_brief_with_import_locations('# Fixture', lines, [], budget_tokens=budget)
    body = server._render_audit_brief_with_import_locations('# Fixture', lines, [carrier_item()], budget_tokens=budget)
    nav = navigation(body)
    if expected: assert nav['status'] == expected
    else: assert nav is None and body == baseline
    assert mandatory(body) == mandatory(baseline) and context_budget_profile(body, budget_tokens=budget)['status'] == 'pass'


def test_optional_item_cap_and_late_priority_do_not_affect_raw_rows():
    items = [carrier_item(count=1) for _ in range(5)]
    policy = evidence.audit_import_location_brief_policy()
    result = evidence.audit_import_location_brief(items, policy)
    assert result['shown_items'] == policy['max_items'] and result['omitted_items'] == 2
    plain = [{'rule': 'non_import', 'target_ref': 'MAIN::other.ts'}]
    assert evidence.audit_import_location_brief(plain, policy) is None


def test_queue_optional_detail_does_not_reduce_the_original_selected_work_items():
    items = [carrier_item(path=f'domain/Panel{i}.ts') for i in range(3)]
    for i, item in enumerate(items):
        item.update(id=f'fixture_{i}', evidence='source evidence ' * 250)
    data = payload(items)
    with patch.object(evidence, 'audit_import_location_brief_policy', side_effect=ValueError('withheld')):
        baseline = server._render_violation_work_queue_brief(data)
    body = server._render_violation_work_queue_brief(data)
    assert mandatory(body) == mandatory(baseline)
    assert context_budget_profile(body, budget_tokens=server.BOUNDED_AGENT_PACKET_TOKENS)['status'] == 'pass'


@pytest.mark.parametrize('invalid', [None, {}, {'version': 'wrong', 'max_items': 3, 'max_locations_per_item': 2, 'claim_boundary': 'boundary'},
    {'version': 'v1-captured-navigation-summary', 'max_items': True, 'max_locations_per_item': 2, 'claim_boundary': 'boundary'}])
def test_invalid_optional_policy_keeps_original_brief(monkeypatch, invalid):
    from tools.core import json_io
    original = json_io.load_json_object_strict_cached
    contract = deepcopy(original(evidence.AUDIT_PRESENTATION_CONTRACT_PATH))
    contract['audit_import_location_brief_policy'] = invalid
    monkeypatch.setattr(json_io, 'load_json_object_strict_cached', lambda path: contract if Path(path) == evidence.AUDIT_PRESENTATION_CONTRACT_PATH else original(path))
    data = payload([carrier_item()])
    before = deepcopy(data)
    body = server._render_module_integrity_brief(data)
    assert 'captured_import_navigation' not in body and data == before and 'Inspect the listed target file' in body


def public_fixture(tmp_path, monkeypatch, *, trust='PASS'):
    item = carrier_item()
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda *_a: tmp_path)
    monkeypatch.setattr(server, '_analysis_root_display', lambda *_a: str(tmp_path))
    monkeypatch.setattr(server, '_analysis_snapshot_id', lambda *_a: 'controlled-snapshot-1')
    monkeypatch.setattr(server, '_audit_findings_generation_projection', lambda *_a, **_k: {})
    monkeypatch.setattr(server, '_ensure_agent_artifact_chain_current', lambda *_a, **_k: {})
    monkeypatch.setattr(server, '_audit_queue_trust_projection', lambda *_a: {'status': trust, 'failures': [], 'warnings': []})
    monkeypatch.setattr(server, '_audit_queue_recovery_plan', lambda **_k: {'status': 'controlled_unavailable'})
    monkeypatch.setattr(server, '_record_mcp_call_result', lambda _n, _s, result, **_k: result)
    monkeypatch.setattr(server, '_resolve_work_item_paths_for_agent', lambda _r, items, **_k: items)
    monkeypatch.setattr(server, '_audit_violation_work_items_from_sqlite', lambda *_a, **_k: ([deepcopy(item)], 1, True))
    monkeypatch.setattr(server, '_module_integrity_items_from_sqlite', lambda *_a, **_k: ([deepcopy(item)], 1, True))
    return item


@pytest.mark.parametrize('tool', ['queue', 'module'])
def test_public_default_brief_and_machine_payload_compatibility_without_source_replay(tmp_path, monkeypatch, tool):
    item = public_fixture(tmp_path, monkeypatch)
    call = (lambda **kw: server.get_violation_work_queue(target_root=str(tmp_path), **kw)) if tool == 'queue' else \
        (lambda **kw: server.check_module_integrity('MAIN::domain/Panel.ts', target_root=str(tmp_path), **kw))
    before = deepcopy(item)
    brief = call(); machine = json.loads(call(format='machine'))
    assert navigation(brief)['items'][0]['status'] == 'partial'
    rows = machine['items'] if tool == 'queue' else machine['reasoning_trace']
    assert rows == [before] and item == before and 'captured_import_navigation' not in machine


@pytest.mark.parametrize('tool', ['queue', 'module'])
def test_public_stale_gate_does_not_show_navigation_or_read_findings(tmp_path, monkeypatch, tool):
    public_fixture(tmp_path, monkeypatch, trust='FAIL')
    def forbidden(*_a, **_k): pytest.fail('Blocked trust must not consume findings')
    monkeypatch.setattr(server, '_audit_violation_work_items_from_sqlite', forbidden)
    monkeypatch.setattr(server, '_module_integrity_items_from_sqlite', forbidden)
    body = server.get_violation_work_queue() if tool == 'queue' else server.check_module_integrity('MAIN::domain/Panel.ts')
    assert 'captured_import_navigation' not in body and 'status: INVALID_CONTEXT' in body and 'blocking: true' in body
