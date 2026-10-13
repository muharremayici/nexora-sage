"""Finite JSX callback source facts; never execute the target module."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tools.engines.generate_atlas import AST_CONTRACT_VERSION, _normalize_polyglot_symbols

ROOT = Path(__file__).resolve().parents[2]
CALLER = 'jsx_prop_callback_evidence'
CHILD = 'jsx_parameter_callback_call_evidence'
PARENTS = {
    'plain': 'const run = () => {}; return <Child run={run} />;',
    'rename': 'const run = () => {}; return <Picked run={run} />;',
    'conditional': 'const run = () => {}; if (flag) return <Child run={run} />; return null;',
    'spread_before': 'const run = () => {}; return <Child {...props} run={run} />;',
    'spread_after': 'const run = () => {}; return <Child run={run} {...props} />;',
    'duplicate': 'const run = () => {}; return <Child run={run} run={run} />;',
    'shadow': 'const Child = () => null; const run = () => {}; return <Child run={run} />;',
    'wrapper': 'const run = () => {}; return <Child run={() => run()} />;',
    'member': 'const run = () => {}; return <NS.Child run={run} />;',
    'type_only': 'const run = () => {}; return <TypeChild run={run} />;',
    'default_import': 'const run = () => {}; return <DefaultChild run={run} />;',
    'let': 'let run = () => {}; return <Child run={run} />;',
    'reassigned': 'const run = () => {}; run = other; return <Child run={run} />;',
    'destructured_write': 'const run = () => {}; ({run} = props); return <Child run={run} />;',
    'nested_write': 'const run = () => {}; const mutate = () => { run = other; }; return <Child run={run} />;',
    'nonnull_write': 'const run = () => {}; run! = other; return <Child run={run} />;',
    'alias': 'const run = () => {}; const alias = run; return <Child run={alias} />;',
    'expression_arrow': 'const run = () => 1; return <Child run={run} />;',
    'deferred': 'const run = () => {}; return wrap(() => <Child run={run} />);',
}
CHILDREN = {
    'Plain': ('{run}', 'return <Button onClick={() => { run(); }} />;'),
    'Awaited': ('{run}', 'return <Button onClick={async () => { await run(); }} />;'),
    'Nested': ('{run}', 'return <Controller render={() => (<Button onClick={() => { run(); }} />)} />;'),
    'Default': ('{run = fallback}', 'return <Button onClick={() => { run(); }} />;'),
    'OtherDefaultWrite': ('{run, unused = (run = replacement)}', 'return <Button onClick={() => { run(); }} />;'),
    'OtherDefault': ('{run, unused = fallback}', 'return <Button onClick={() => { run(); }} />;'),
    'Rest': ('{run, ...rest}', 'return <Button onClick={() => { run(); }} />;'),
    'Alias': ('{run: alias}', 'return <Button onClick={() => { alias(); }} />;'),
    'Optional': ('{run}', 'return <Button onClick={() => { run?.(); }} />;'),
    'Shadow': ('{run}', 'return <Button onClick={(run: any) => { run(); }} />;'),
    'Reassigned': ('{run}', 'run = other; return <Button onClick={() => { run(); }} />;'),
    'NestedWrite': ('{run}', 'const mutate = () => { ({handler: run} = props); }; return <Button onClick={() => { run(); }} />;'),
    'ForWrite': ('{run}', 'for (run of handlers) {} return <Button onClick={() => { run(); }} />;'),
    'Branch': ('{run}', 'return <Button onClick={() => { if (flag) run(); }} />;'),
    'Deferred': ('{run}', 'return <Button onClick={() => { wrap(() => { run(); }); }} />;'),
    'ThirdLayer': ('{run}', 'return <A render={() => <B render={() => <C onClick={() => { run(); }} />} />} />;'),
    'Cast': ('{run}', 'return <Button onClick={() => { (run as any)(); }} />;'),
}


def _native(path):
    node = shutil.which('node')
    assert node
    run = subprocess.run([node, str(ROOT / 'tools/engines/ast_sequencer.cjs'), str(path)], cwd=ROOT,
                         capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.fixture(scope='module')
def jsx_inputs(tmp_path_factory):
    source = '\ufeff// \U0001f680 Unicode/CRLF\r\n' + '\r\n'.join([
        "import {Child, Child as Picked} from './child';",
        "import * as NS from './child'; import type {Child as TypeChild} from './child';",
        "import DefaultChild from './child';",
        "import {Child as Spare} from './other';",
        *[f'export function Parent_{name}() {{ {body} }}' for name, body in PARENTS.items()],
        *[f'export const Child_{name} = ({param}: any) => {{ {body} }};' for name, (param, body) in CHILDREN.items()],
        'export const Child_Expression = ({run}: any) => <Button onClick={() => { run(); }} />;',
        "throw new Error('Target must never execute');",
    ])
    path = tmp_path_factory.mktemp('jsx-native') / 'Cases.tsx'
    path.write_bytes(source.encode('utf-8'))
    raw = _native(path)
    assert next(row for row in raw if row['name'] == '__file_meta__')['parserStatus'] == 'observed'
    rows = {row['name']: row for row in _normalize_polyglot_symbols(raw, source, 'typescript')}
    return source, raw, rows


@pytest.mark.parametrize('name', ['plain', 'rename', 'conditional'])
def test_jsx_caller_native_binding_and_bytes(jsx_inputs, name):
    source, _, rows = jsx_inputs
    lane = rows['Parent_' + name][CALLER]
    assert lane['status'] == 'observed' and len(lane['records']) == 1
    assert lane['ast_contract_version'] == AST_CONTRACT_VERSION
    assert lane['source_hash'] == hashlib.sha256(source.encode('utf-8')).hexdigest()
    row = lane['records'][0]
    assert row['source'] == './child' and row['importedName'] == 'Child'
    assert row['callback_name'] == row['prop'] == 'run'
    for role, text in [('value', 'run'), ('callback_binding', 'run'), ('prop', 'run')]:
        span = row['spans'][role]
        assert source.encode('utf-8')[span['start']:span['end']].decode('utf-8') == text
    assert lane['runtime_execution'] == 'not_established'


@pytest.mark.parametrize('name', [name for name in PARENTS if name not in {'plain', 'rename', 'conditional'}])
def test_jsx_caller_refuses_unsupported_bindings(jsx_inputs, name):
    lane = jsx_inputs[2]['Parent_' + name][CALLER]
    assert lane['status'] == 'observed' and lane['records'] == []


@pytest.mark.parametrize('name,contexts', [('Plain', ['onClick']), ('Awaited', ['onClick']),
                                         ('Nested', ['render', 'onClick']), ('Expression', ['onClick'])])
def test_jsx_child_native_parameter_and_context(jsx_inputs, name, contexts):
    source, _, rows = jsx_inputs
    lane = rows['Child_' + name][CHILD]
    assert lane['status'] == 'observed' and len(lane['records']) == 1
    row = lane['records'][0]
    assert row['context_attributes'] == contexts and row['awaited'] == (name == 'Awaited')
    assert row['prop'] == row['binding_name'] == 'run'
    for role in ['binding', 'property', 'callee']:
        span = row['spans'][role]
        assert source.encode('utf-8')[span['start']:span['end']].decode('utf-8') == 'run'
    assert lane['runtime_execution'] == 'not_established'


@pytest.mark.parametrize('name', [name for name in CHILDREN if name not in {'Plain', 'Awaited', 'Nested'}])
def test_jsx_child_refuses_unsupported_bindings(jsx_inputs, name):
    lane = jsx_inputs[2]['Child_' + name][CHILD]
    assert lane['status'] == 'observed' and lane['records'] == []


@pytest.mark.parametrize('key,owner', [(CALLER, 'Parent_plain'), (CHILD, 'Child_Nested')])
@pytest.mark.parametrize('damage', ['hash', 'parser', 'version', 'authority', 'status_type', 'span_type', 'line', 'duplicate', 'containment', 'prop_text', 'binding_text'])
def test_jsx_normalizer_refuses_malformed(jsx_inputs, key, owner, damage):
    source, original, _ = jsx_inputs
    raw = deepcopy(original)
    selected = next(row for row in raw if row['name'] == owner)
    lane = selected[key]
    if damage == 'hash': lane['source_hash'] = '0' * 64
    elif damage == 'parser': selected['parserKind'] = 'degraded'
    elif damage == 'version': lane['version'] = 'unknown'
    elif damage == 'authority': lane['runtime_execution'] = 'proven'
    elif damage == 'status_type': lane['status'] = []
    elif damage == 'span_type': lane['records'][0]['spans']['callee' if key == CHILD else 'value']['start'] = True
    elif damage == 'line': lane['records'][0]['spans']['callee' if key == CHILD else 'value']['line'] += 1
    elif damage == 'duplicate': lane['records'].append(deepcopy(lane['records'][0]))
    elif damage == 'containment': lane['records'][0]['spans']['callee' if key == CHILD else 'value'] = deepcopy(lane['records'][0]['spans']['parameter' if key == CHILD else 'callback'])
    elif damage == 'prop_text':
        lane['records'][0]['prop'] = 'other'
        if key == CHILD: lane['records'][0]['binding_name'] = 'other'
    elif damage == 'binding_text':
        if key == CHILD: lane['records'][0]['context_attributes'][0] = 'wrong'
        else: lane['records'][0]['localName'] = 'OtherChild'
    normalized = next(row for row in _normalize_polyglot_symbols(raw, source, 'typescript') if row['name'] == owner)[key]
    assert normalized['status'] == 'unavailable' and normalized['records'] == []


def test_jsx_legacy_metadata_is_not_synthesized(jsx_inputs):
    source, original, _ = jsx_inputs
    raw = deepcopy(original)
    for row in raw:
        row.pop(CALLER, None)
        row.pop(CHILD, None)
    assert all(CALLER not in row and CHILD not in row for row in _normalize_polyglot_symbols(raw, source, 'typescript'))


def test_jsx_coordinates_use_one_shared_batch(jsx_inputs, monkeypatch):
    from tools.engines import generate_atlas
    source, raw, _ = jsx_inputs
    original = generate_atlas._utf16_offsets_to_utf8_byte_offsets
    visits = []
    def count(text, offsets):
        visits.append(len(offsets))
        return original(text, offsets)
    monkeypatch.setattr(generate_atlas, '_utf16_offsets_to_utf8_byte_offsets', count)
    normalized = generate_atlas._normalize_polyglot_symbols(raw, source, 'typescript')
    assert len(visits) == 1 and normalized


@pytest.mark.parametrize('damage', ['module_token_swap', 'missing', 'duplicate', 'wrong_source', 'type_only'])
def test_jsx_import_source_is_same_declaration_and_native_binding(jsx_inputs, damage):
    source, original, _ = jsx_inputs
    raw = deepcopy(original)
    selected = next(row for row in raw if row['name'] == 'Parent_plain')
    row = selected[CALLER]['records'][0]
    meta = next(row for row in raw if row['name'] == '__file_meta__')
    if damage == 'module_token_swap':
        row['source'] = './other'
        start = len(source[:source.index("'./other'")].encode('utf-16-le')) // 2
        line = source[:source.index("'./other'")].count('\n') + 1
        row['spans']['module_source'] = {'start': start, 'end': start + len("'./other'"), 'line': line, 'end_line': line}
    elif damage == 'missing': meta.pop('directImportBindings')
    else:
        binding = next(row for row in meta['directImportBindings'] if row['localName'] == 'Child')
        if damage == 'duplicate': meta['directImportBindings'].append(deepcopy(binding))
        elif damage == 'wrong_source': binding['source'] = './other'
        elif damage == 'type_only': binding['typeOnly'] = True
    normalized = next(row for row in _normalize_polyglot_symbols(raw, source, 'typescript') if row['name'] == 'Parent_plain')[CALLER]
    assert normalized['status'] == 'unavailable' and normalized['records'] == []


@pytest.fixture(scope='module')
def jsx_caps(tmp_path_factory):
    source = "import {Child} from './child';\n" + '\n'.join([
        'export function Parent() { const run = () => {}; return <Child ' +
        ' '.join(f'p{index}={{run}}' for index in range(96)) + ' />; }',
        'export const Receiver = ({run}: any) => <Button onClick={() => { ' + 'run();' * 96 + ' }} />;',
        'export function Large() { const run = () => {}; const xs = [' + ','.join('1' for _ in range(10010)) +
        ']; return <Child run={run} />; }',
    ])
    path = tmp_path_factory.mktemp('jsx-caps') / 'Caps.tsx'
    path.write_bytes(source.encode('utf-8'))
    raw = _native(path)
    return {row['name']: row for row in _normalize_polyglot_symbols(raw, source, 'typescript')}


@pytest.mark.parametrize('owner,key', [('Parent', CALLER), ('Receiver', CHILD)])
def test_jsx_record_cap_is_explicit_partial(jsx_caps, owner, key):
    lane = jsx_caps[owner][key]
    assert lane['status'] == 'incomplete_scan' and len(lane['records']) == 64 and lane['omitted'] == 32


def test_jsx_node_cap_is_unavailable_not_empty(jsx_caps):
    assert jsx_caps['Large'][CALLER]['status'] == 'unavailable'
    assert jsx_caps['Large'][CALLER]['records'] == []


def test_jsx_parse_diagnostics_never_claim_observed(tmp_path):
    source = 'export const Child = ({run}: any) => <Button onClick={() => { run(); }} />; const broken = ;'
    path = tmp_path / 'Broken.tsx'
    path.write_bytes(source.encode('utf-8'))
    raw = _native(path)
    child = next(row for row in _normalize_polyglot_symbols(raw, source, 'typescript') if row['name'] == 'Child')
    assert child[CALLER]['status'] == child[CHILD]['status'] == 'unavailable'


@pytest.fixture(scope='module')
def jsx_atlas_run(tmp_path_factory):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    target = tmp_path_factory.mktemp('jsx-atlas')
    sources = {
        'Panel.tsx': """import {Child as Picked} from './Child';
export function Parent() {
  const target = () => {};
  const run = async () => { await target(); };
  return <Picked run={run} />;
}
throw new Error('Target must never execute');
""",
        'Child.tsx': """export const Child = ({run}: any) => {
  return <Controller render={() => <Button onClick={async () => { await run(); run(); }} />} />;
};
throw new Error('Target must never execute');
""",
    }
    for rel, source in sources.items():
        (target / rel).write_bytes(('\ufeff// \U0001f680\r\n' + source.replace('\n', '\r\n')).encode('utf-8'))
    env = dict(os.environ)
    for key in ('CODEMAPS_TARGET_PREFLIGHT_RECEIPT', 'CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256',
                'CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE', 'CODEMAPS_TARGET_PROJECTS'):
        env.pop(key, None)
    env['CODEMAPS_TARGET_ROOT'] = str(target)
    run = subprocess.run([sys.executable, '-B', '-m', 'tools.engines.generate_atlas'], cwd=ROOT,
                         env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert run.returncode == 0, run.stderr or run.stdout
    raw = ROOT / 'output/external_targets' / external_target_output_slug(str(target)) / '.raw'
    atlas = load_atlas_data(raw)
    assert set(atlas['MAIN']['files']) == set(sources)
    return target, raw, atlas


def _jsx_join(atlas, limit=4, budget=1000):
    from tools.core.state_flow import project_state_flow_focus, _focus_jsx_prop_callbacks
    deferred = {}
    focus = project_state_flow_focus(atlas, project='MAIN', file='Panel.tsx', symbol='Parent',
                                    max_items=4, scan_limit=1000, _deferred_jsx_callbacks=deferred)
    assert focus['status'] == 'selected'
    if not deferred:
        record = atlas['MAIN']['files']['Panel.tsx']
        deferred = {'record': record, 'symbol': next(row for row in record['symbols'] if row['name'] == 'Parent'),
                    'imports': focus['import_candidates'], 'files': atlas['MAIN']['files']}
    return _focus_jsx_prop_callbacks(deferred['record'], deferred['symbol'], deferred['imports'],
                                    deferred['files'], 'MAIN', limit, budget)


def test_jsx_join_consumer_exact_site_and_shared_child_cap(jsx_atlas_run):
    _, _, atlas = jsx_atlas_run
    lane = _jsx_join(atlas, limit=1)
    assert lane['status'] == 'observed' and lane['returned'] == 1
    item = lane['items'][0]
    assert item['parent']['localName'] == 'Picked'
    assert item['child']['target']['atlas_ref'] == 'MAIN::Child.tsx'
    assert item['child']['status'] == 'source_candidate'
    assert item['child']['returned'] == item['child']['omitted'] == 1
    assert item['child']['items'][0]['context_attributes'] == ['render', 'onClick']
    assert lane['runtime_execution'] == lane['value_transfer'] == 'not_established'
    # The JSX site is not manufactured as a direct CallExpression.
    from tools.core.state_flow import project_state_flow_focus
    focus = project_state_flow_focus(atlas, project='MAIN', file='Panel.tsx', symbol='Parent',
                                    max_items=4, scan_limit=1000)
    assert focus['direct_callees']['items'] == []
    assert focus['symbol_context']['jsx_prop_callbacks']['returned'] == 1


@pytest.mark.parametrize('endpoint', ['parent', 'child'])
@pytest.mark.parametrize('damage', ['missing', 'hash', 'ast', 'parser', 'parser_kind', 'not_callable',
                                  'authority', 'span', 'duplicate', 'incomplete', 'outer'])
def test_jsx_join_consumer_refuses_bad_endpoint(jsx_atlas_run, endpoint, damage):
    atlas = deepcopy(jsx_atlas_run[2])
    record = atlas['MAIN']['files']['Panel.tsx' if endpoint == 'parent' else 'Child.tsx']
    symbol = next(row for row in record['symbols'] if row['name'] == ('Parent' if endpoint == 'parent' else 'Child'))
    key = CALLER if endpoint == 'parent' else CHILD
    evidence = symbol[key]
    if damage == 'missing': symbol.pop(key)
    elif damage == 'hash': evidence['source_hash'] = '0' * 64
    elif damage == 'ast': record['ast_contract_version'] = 'old'
    elif damage == 'parser': record['parser_evidence']['status'] = 'degraded'
    elif damage == 'parser_kind': symbol['parser_kind'] = 'degraded'
    elif damage == 'not_callable': symbol['type'] = 'Variable'
    elif damage == 'authority': evidence['runtime_execution'] = 'proven'
    elif damage == 'span': evidence['records'][0]['spans']['binding' if endpoint == 'child' else 'value']['end'] = True
    elif damage == 'duplicate': evidence['records'].append(deepcopy(evidence['records'][0]))
    elif damage == 'incomplete': evidence.update(status='incomplete_scan', omitted=1)
    elif damage == 'outer': symbol['start'] = True
    lane = _jsx_join(atlas)
    refused = lane if endpoint == 'parent' else lane['items'][0]['child']
    assert refused['status'] in {'unavailable', 'incomplete_scan', 'unresolved'}
    assert refused.get('items', []) == []


@pytest.mark.parametrize('damage', ['missing_import', 'duplicate_import', 'type_only', 'wrong_target',
                                  'duplicate_export', 'reexport', 'not_exported', 'different_prop', 'budget'])
def test_jsx_join_module_export_and_budget_boundaries(jsx_atlas_run, damage):
    atlas = deepcopy(jsx_atlas_run[2])
    files = atlas['MAIN']['files']
    parent, child = files['Panel.tsx'], files['Child.tsx']
    symbol = next(row for row in child['symbols'] if row['name'] == 'Child')
    if damage == 'missing_import': parent['import_records'] = []
    elif damage == 'duplicate_import': parent['import_records'] *= 2
    elif damage == 'type_only': parent['import_records'][0]['kind'] = 'type'
    elif damage == 'wrong_target': parent['import_records'][0]['source'] = '../outside/Child.tsx'
    elif damage == 'duplicate_export': child['symbols'].append(deepcopy(symbol))
    elif damage == 'reexport': symbol['type'] = 'ReExportedSymbol'
    elif damage == 'not_exported': symbol['exported'] = False
    elif damage == 'different_prop':
        # Source-fact identities remain unchanged; only choose a valid caller
        # fact for a different child property in the bounded join.
        from tools.core.state_flow import _focus_jsx_prop_callbacks
        modules = {'items': [{'source': './Child', 'localName': 'Picked', 'importedName': 'Child', 'kind': 'named',
                             'status': 'target_candidate', 'target': {'atlas_ref': 'MAIN::Child.tsx',
                             'source_hash': child['hash']}}], 'status': 'observed', 'omitted': 0}
        original = next(row for row in parent['symbols'] if row['name'] == 'Parent')
        unrelated = deepcopy(original)
        unrelated[CALLER]['records'][0]['prop'] = 'other'
        lane = _focus_jsx_prop_callbacks(parent, unrelated, modules, files, 'MAIN', 4, 1000)
        unmatched = lane['items'][0]['child']
        assert unmatched['status'] == 'unresolved' and unmatched['items'] == []
        assert unmatched['reason'] == 'no_admitted_same_prop_call'
        assert unmatched['absence_claim'] == 'not_established'
        return
    lane = _jsx_join(atlas, budget=0 if damage == 'budget' else 1000)
    if damage == 'budget': assert lane['status'] == 'incomplete_scan' and lane['items'] == []
    else:
        refused = lane['items'][0]['child']
        assert refused['status'] != 'source_candidate' and refused['items'] == []


def _jsx_mcp_fixture(jsx_atlas_run, tmp_path, monkeypatch, damage='none'):
    from tools.core.artifact_store import ArtifactStore
    from tools.mcp import server
    target, _, original = jsx_atlas_run
    local = tmp_path / 'target'
    local.mkdir()
    for rel in ('Panel.tsx', 'Child.tsx'):
        (local / rel).write_bytes((target / rel).read_bytes())
    atlas = deepcopy(original)
    atlas['MAIN']['root_path'] = str(local)
    if damage == 'legacy':
        for row in atlas['MAIN']['files']['Panel.tsx']['symbols']: row.pop(CALLER, None)
    raw = tmp_path / '.raw'
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, 'hybrid_sqlite'
    store._ensure_schema()
    store.state_payload_inline_limit_bytes = 1
    store._save_payload_to_state_table('atlas', atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / 'codemaps.db') as connection:
        connection.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage == 'corrupt_primary': connection.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
    # A tempting compatibility shadow must not rescue a corrupt primary.
    (raw / 'atlas.json').write_text(json.dumps(atlas), encoding='utf-8')
    if damage in {'parent_drift', 'child_drift'}:
        path = local / ('Panel.tsx' if damage == 'parent_drift' else 'Child.tsx')
        path.write_bytes(path.read_bytes() + b'\n// drift')
    if damage == 'packet_budget': monkeypatch.setattr(server, 'DEFAULT_AGENT_PACKET_BUDGET_TOKENS', 300)
    server._ATLAS_CACHE.clear()
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda root: raw)
    monkeypatch.setattr(server, '_analysis_root_display', lambda root: str(local))
    return local, raw, atlas


@pytest.mark.parametrize('damage', ['none', 'legacy', 'parent_drift', 'child_drift', 'corrupt_primary', 'packet_budget'])
def test_jsx_primary_mcp_endpoint_and_packet_controls(jsx_atlas_run, tmp_path, monkeypatch, damage):
    from tools.mcp import server
    local, _, _ = _jsx_mcp_fixture(jsx_atlas_run, tmp_path, monkeypatch, damage)
    result = json.loads(server.get_state_flow(file='Panel.tsx', symbol='Parent', target_root=str(local), format='json'))
    lane = result.get('symbol_context', {}).get('jsx_prop_callbacks')
    if damage in {'legacy', 'corrupt_primary', 'packet_budget'}:
        assert lane is None or lane['returned'] == 0
        if damage == 'packet_budget':
            assert 'absence' in result['next_action'] and 'JSX' in result['next_action']
    elif damage == 'parent_drift':
        assert lane['status'] == 'unavailable' and lane['items'] == []
    elif damage == 'child_drift':
        assert lane['returned'] == 1 and lane['items'][0]['child']['status'] == 'unavailable'
        assert lane['items'][0]['child']['items'] == []
    else:
        assert lane['status'] == 'observed' and lane['returned'] == 1
        child = lane['items'][0]['child']
        assert child['endpoint_content_binding'] == 'both_snapshot_and_live_match'
        assert child['returned'] == 2 and child['omitted'] == 0
        assert child['items'][0]['awaited'] is True
        assert lane['source_binding'] == child['source_binding'] == 'snapshot_and_live_match'
        assert result['symbol_context']['local_arrow_sibling_calls']['returned'] == 1
        assert lane['runtime_execution'] == child['runtime_execution'] == 'not_established'
        brief = server.get_state_flow(file='Panel.tsx', symbol='Parent', target_root=str(local), max_items=1)
        assert 'jsx_prop_callbacks' in brief


def test_jsx_late_scan_budget_preserves_prior_lane(jsx_atlas_run, tmp_path, monkeypatch):
    from tools.mcp import server
    local, _, _ = _jsx_mcp_fixture(jsx_atlas_run, tmp_path, monkeypatch)
    baseline = json.loads(server.get_state_flow(file='Panel.tsx', symbol='Parent', target_root=str(local), format='json'))
    base_policy = server._state_flow_brief_policy()
    keys = list(baseline['symbol_context'])
    remaining = baseline['visited_records'] - sum(
        baseline['symbol_context'][key].get('visited_records', 0)
        for key in keys[keys.index('jsx_prop_callbacks'):])
    monkeypatch.setattr(server, '_state_flow_brief_policy', lambda: {**base_policy, 'focus_scan_limit': remaining})
    limited = json.loads(server.get_state_flow(file='Panel.tsx', symbol='Parent', target_root=str(local), format='json'))
    assert limited['symbol_context']['local_arrow_sibling_calls'] == baseline['symbol_context']['local_arrow_sibling_calls']
    lane = limited['symbol_context']['jsx_prop_callbacks']
    assert lane['status'] == 'incomplete_scan' and lane['items'] == []
    assert limited['visited_records'] <= remaining


def test_jsx_child_index_scan_exhaustion_discloses_withheld_records(jsx_atlas_run):
    lane = _jsx_join(jsx_atlas_run[2])
    child = lane['items'][0]['child']
    cut = lane['visited_records'] - child['returned']
    bounded = _jsx_join(jsx_atlas_run[2], budget=cut)
    refused = bounded['items'][0]['child']
    assert refused['status'] == bounded['status'] == 'incomplete_scan'
    assert refused['items'] == [] and refused['omitted'] == child['returned']
    assert bounded['visited_records'] <= cut


def test_jsx_packet_omission_notice_does_not_enlarge_settled_packet(jsx_atlas_run, tmp_path, monkeypatch):
    from tools.mcp import server
    from tools.core.agent_packet_budget import context_budget_profile
    local, _, _ = _jsx_mcp_fixture(jsx_atlas_run, tmp_path, monkeypatch)
    baseline = json.loads(server.get_state_flow(file='Panel.tsx', symbol='Parent', target_root=str(local), format='json'))
    # This cap protects the pre-JSX packet, not later additive metadata.
    keys = list(baseline['symbol_context'])
    for key in keys[keys.index('jsx_prop_callbacks'):]:
        baseline['symbol_context'].pop(key)
    budget = context_budget_profile(json.dumps(baseline, ensure_ascii=False))['estimated_tokens']
    monkeypatch.setattr(server, 'DEFAULT_AGENT_PACKET_BUDGET_TOKENS', budget)
    limited = json.loads(server.get_state_flow(file='Panel.tsx', symbol='Parent', target_root=str(local), format='json'))
    assert 'jsx_prop_callbacks' not in limited['symbol_context']
    assert context_budget_profile(json.dumps(limited, ensure_ascii=False), budget_tokens=budget)['status'] == 'pass'
    assert limited['symbol_context']['local_arrow_sibling_calls'] == baseline['symbol_context']['local_arrow_sibling_calls']


def test_jsx_mcp_primary_checks_share_scan_budget_and_endpoint_cache(jsx_atlas_run, tmp_path, monkeypatch):
    from tools.core.artifact_store import ArtifactStore
    from tools.mcp import server
    local, _, _ = _jsx_mcp_fixture(jsx_atlas_run, tmp_path, monkeypatch)
    original = ArtifactStore._state_payload_stream
    reads = []
    def counted(store, conn, row):
        reads.append(row['name'])
        return original(store, conn, row)
    monkeypatch.setattr(ArtifactStore, '_state_payload_stream', counted)
    baseline = json.loads(server.get_state_flow(file='Panel.tsx', symbol='Parent', target_root=str(local), format='json'))
    assert reads == ['atlas']
    policy = server._state_flow_brief_policy()
    # Exhaust the JSX endpoint check, not the later returned-callback lane.
    keys = list(baseline['symbol_context'])
    cap = baseline['visited_records'] - 1 - sum(
        baseline['symbol_context'][key].get('visited_records', 0)
        for key in keys[keys.index('jsx_prop_callbacks') + 1:])
    reads.clear()
    monkeypatch.setattr(server, '_state_flow_brief_policy', lambda: {**policy, 'focus_scan_limit': cap})
    limited = json.loads(server.get_state_flow(file='Panel.tsx', symbol='Parent', target_root=str(local), format='json'))
    assert reads == ['atlas']
    assert limited['visited_records'] <= cap
    lane = limited['symbol_context']['jsx_prop_callbacks']
    assert lane['status'] == 'incomplete_scan'
    assert lane['items'][0]['child']['status'] == 'incomplete_scan'
    assert lane['items'][0]['child']['omitted'] == 2


def test_jsx_primary_corruption_with_warm_cache_refuses_earlier_sibling_syntax(jsx_atlas_run, tmp_path, monkeypatch):
    from tools.mcp import server
    local, raw, atlas = _jsx_mcp_fixture(jsx_atlas_run, tmp_path, monkeypatch)
    baseline = json.loads(server.get_state_flow(file='Panel.tsx', symbol='Parent', target_root=str(local), format='json'))
    assert baseline['symbol_context']['local_arrow_sibling_calls']['items'][0]['awaited'] is True
    shadow = deepcopy(atlas)
    parent = next(row for row in shadow['MAIN']['files']['Panel.tsx']['symbols'] if row['name'] == 'Parent')
    wrong = parent['local_arrow_sibling_call_evidence']['records'][0]
    wrong['awaited'] = False
    wrong['spans']['statement']['start'] = wrong['spans']['call']['start']
    (raw / 'atlas.json').write_text(json.dumps(shadow), encoding='utf-8')
    with sqlite3.connect(raw / 'codemaps.db') as conn:
        conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
    result = json.loads(server.get_state_flow(file='Panel.tsx', symbol='Parent', target_root=str(local), format='json'))
    assert result['status'] == 'unavailable' and result['source_binding'] == 'unavailable'
    assert result.get('symbol_context', {}).get('local_arrow_sibling_calls', {}).get('items', []) == []
    assert 'absence proof' in result['next_action']
