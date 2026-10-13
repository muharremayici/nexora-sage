"""Finite local-sibling syntax, not callback activation or runtime proof."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tools.engines.generate_atlas import AST_CONTRACT_VERSION, _normalize_polyglot_symbols

ROOT = Path(__file__).resolve().parents[2]
KEY = 'local_arrow_sibling_call_evidence'
CASES = {
    'plain': "const target = (value: string) => { return value; }; const caller = () => { target('x'); };",
    'awaited': "const target = async () => { return 1; }; const caller = async () => { await target(); };",
    'forward': "const caller = () => { target(); }; const target = () => { return 1; };",
    'let_target': "let target = () => {}; const caller = () => { target(); };",
    'let_caller': "const target = () => {}; let caller = () => { target(); };",
    'duplicate': "const target = () => {}; const target = () => {}; const caller = () => { target(); };",
    'shadow': "const target = () => {}; const caller = (target: any) => { target(); };",
    'alias': "const target = () => {}; const alias = target; const caller = () => { alias(); };",
    'member': "const target = () => {}; const caller = () => { target.call(null); };",
    'optional': "const target = () => {}; const caller = () => { target?.(); };",
    'branch': "const target = () => {}; const caller = () => { if (true) { target(); } };",
    'loop': "const target = () => {}; const caller = () => { for (;;) { target(); } };",
    'deferred': "const target = () => {}; const caller = () => { Promise.resolve().then(() => target()); };",
    'target_expression': "const target = () => 1; const caller = () => { target(); };",
    'caller_expression': "const target = () => {}; const caller = () => target();",
    'cast': "const target = () => {}; const caller = () => { (target as any)(); };",
    'function': "function target() {} const caller = () => { target(); };",
    'multi': "const target = () => {}, other = 1; const caller = () => { target(); };",
    'reassigned': "const target = () => {}; const caller = () => { target(); }; target = () => {};",
    'array_write': "const target = () => {}; const caller = () => { target(); }; ([target] = replacements);",
    'object_write': "const target = () => {}; const caller = () => { target(); }; ({handler: target} = replacement);",
    'shorthand_write': "const target = () => {}; const caller = () => { target(); }; ({target} = replacement);",
    'caller_write': "const target = () => {}; const caller = () => { target(); }; ({handler: caller} = replacement);",
    'loop_write': "const target = () => {}; const caller = () => { target(); }; for (target of replacements) {}",
    'nonnull_write': "const target = () => {}; const caller = () => { target(); }; target! = replacement;",
    'nested': "const target = () => {}; const holder = () => { const caller = () => { target(); }; };",
}


def _native(path):
    node = shutil.which('node')
    assert node, 'Native contract requires the configured Node runtime'
    run = subprocess.run([node, str(ROOT / 'tools/engines/ast_sequencer.cjs'), str(path)],
                         cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.fixture(scope='module')
def sibling_inputs(tmp_path_factory):
    source = '\ufeff// \U0001f680 exact Unicode/CRLF identity\r\n' + '\r\n'.join(
        f'export function Case_{name}() {{ {body} }}' for name, body in CASES.items())
    path = tmp_path_factory.mktemp('local-sibling-native') / 'Cases.ts'
    path.write_bytes(source.encode('utf-8'))
    raw = _native(path)
    normalized = _normalize_polyglot_symbols(raw, source, 'typescript')
    return source, raw, {row['name']: row for row in normalized}


@pytest.mark.parametrize('name', ['plain', 'awaited', 'forward'])
def test_native_sibling_positive(sibling_inputs, name):
    source, raw, rows = sibling_inputs
    evidence = rows['Case_' + name][KEY]
    assert evidence['status'] == 'observed' and evidence['omitted'] == 0
    assert evidence['source_hash'] == hashlib.sha256(source.encode('utf-8')).hexdigest()
    assert evidence['ast_contract_version'] == AST_CONTRACT_VERSION
    assert len(evidence['records']) == 1
    row = evidence['records'][0]
    assert (row['caller_name'], row['target_name'], row['awaited']) == ('caller', 'target', name == 'awaited')
    content = source.encode('utf-8')
    for role, text in [('owner_binding', 'caller'), ('target_binding', 'target'), ('callee', 'target')]:
        span = row['spans'][role]
        assert content[span['start']:span['end']].decode('utf-8') == text
    outer = next(item for item in raw if item['name'] == 'Case_' + name)
    assert not any(item['name'] in {'caller', 'target'} and outer['start'] < item['start'] < outer['end'] for item in raw)
    assert evidence['runtime_execution'] == 'not_established'


@pytest.mark.parametrize('name', [name for name in CASES if name not in {'plain', 'awaited', 'forward'}])
def test_native_sibling_unsupported_is_not_clean(sibling_inputs, name):
    lane = sibling_inputs[2]['Case_' + name][KEY]
    assert lane['status'] == 'observed' and lane['records'] == []
    assert lane['runtime_execution'] == 'not_established'


@pytest.mark.parametrize('damage', ['hash', 'version', 'extra_authority', 'bool_span', 'containment', 'duplicate', 'status_type'])
def test_sibling_normalizer_refuses_malformed(sibling_inputs, damage):
    source, original, _ = sibling_inputs
    raw = deepcopy(original)
    owner = next(row for row in raw if row['name'] == 'Case_plain')
    lane = owner[KEY]
    if damage == 'hash': lane['source_hash'] = '0' * 64
    elif damage == 'version': lane['version'] = 'old'
    elif damage == 'extra_authority': lane['runtime_execution'] = 'proven'
    elif damage == 'bool_span': lane['records'][0]['spans']['call']['start'] = True
    elif damage == 'containment': lane['records'][0]['spans']['target'] = deepcopy(lane['records'][0]['spans']['owner'])
    elif damage == 'duplicate': lane['records'].append(deepcopy(lane['records'][0]))
    elif damage == 'status_type': lane['status'] = []
    result = next(row for row in _normalize_polyglot_symbols(raw, source, 'typescript') if row['name'] == 'Case_plain')[KEY]
    assert result['status'] == 'unavailable' and result['records'] == []


def _record(source, rows):
    return {'hash': hashlib.sha256(source.encode('utf-8')).hexdigest(), 'size': len(source.encode('utf-8')),
            'loc': len(source.splitlines()), 'parser_evidence': {'status': 'observed'},
            'ast_contract_version': AST_CONTRACT_VERSION, 'symbols': list(rows.values())}


@pytest.mark.parametrize('damage', ['none', 'missing', 'hash', 'ast', 'parser', 'authority', 'bool_span',
                                  'containment', 'duplicate', 'negative_omitted', 'incomplete', 'budget',
                                  'callee_line', 'call_line', 'statement_line', 'binding_line'])
def test_sibling_projection_identity_and_scan_refusal(sibling_inputs, damage):
    from tools.core.state_flow import _focus_local_arrow_sibling_calls
    source, _, original = sibling_inputs
    rows = deepcopy(original)
    owner = rows['Case_plain']
    record = _record(source, rows)
    lane = owner[KEY]
    if damage == 'missing': owner.pop(KEY)
    elif damage == 'hash': lane['source_hash'] = '0' * 64
    elif damage == 'ast': record['ast_contract_version'] = 'old'
    elif damage == 'parser': record['parser_evidence']['status'] = 'degraded'
    elif damage == 'authority': lane['promise_resolution'] = 'proven'
    elif damage == 'bool_span': lane['records'][0]['spans']['target']['end'] = True
    elif damage == 'containment': lane['records'][0]['spans']['target'] = deepcopy(lane['records'][0]['spans']['owner'])
    elif damage == 'duplicate': lane['records'].append(deepcopy(lane['records'][0]))
    elif damage == 'negative_omitted': lane['omitted'] = -1
    elif damage == 'incomplete': lane.update(status='incomplete_scan', omitted=1)
    elif damage in {'callee_line', 'call_line', 'statement_line', 'binding_line'}:
        role = {'callee_line': 'callee', 'call_line': 'call', 'statement_line': 'statement', 'binding_line': 'owner_binding'}[damage]
        span = lane['records'][0]['spans'][role]
        span['line'] = span['end_line'] = record['loc']
    result = _focus_local_arrow_sibling_calls(record, owner, 3, 0 if damage == 'budget' else 64)
    if damage == 'none':
        assert result['status'] == 'observed' and result['returned'] == 1
        assert result['items'][0]['target_name'] == 'target'
    else:
        assert result['status'] in {'unavailable', 'incomplete_scan'} and result['items'] == []
    assert result['runtime_execution'] == result['value_transfer'] == 'not_established'


@pytest.fixture(scope='module')
def sibling_atlas_run(tmp_path_factory):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    target = tmp_path_factory.mktemp('local-sibling-e2e')
    source = """export function Outer() {
  const target = async (value: string) => { return value; };
  const caller = async () => { await target('first'); await target('second'); };
  return null;
}
throw new Error('Target source must never be executed');
"""
    (target / 'Panel.ts').write_bytes(('\ufeff// \U0001f680\r\n' + source.replace('\n', '\r\n')).encode('utf-8'))
    env = dict(os.environ)
    for key in ('CODEMAPS_TARGET_PREFLIGHT_RECEIPT', 'CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256',
                'CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE', 'CODEMAPS_TARGET_PROJECTS'):
        env.pop(key, None)
    env['CODEMAPS_TARGET_ROOT'] = str(target)
    run = subprocess.run([sys.executable, '-B', '-m', 'tools.engines.generate_atlas'], cwd=ROOT,
                         env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert run.returncode == 0, run.stderr or run.stdout
    raw = ROOT / 'output/external_targets' / external_target_output_slug(str(target)) / '.raw'
    return target, raw, load_atlas_data(raw)


def test_sibling_actual_sqlite_mcp_brief_and_item_cap(sibling_atlas_run):
    from tools.mcp import server
    target, raw, atlas = sibling_atlas_run
    owner = next(row for row in atlas['MAIN']['files']['Panel.ts']['symbols'] if row['name'] == 'Outer')
    assert len(owner[KEY]['records']) == 2
    server._ATLAS_CACHE.clear()
    machine = json.loads(server.get_state_flow(file='Panel.ts', symbol='Outer', target_root=str(target), format='json', max_items=1))
    lane = machine['symbol_context']['local_arrow_sibling_calls']
    assert lane['status'] == 'observed' and lane['returned'] == lane['omitted'] == 1
    assert lane['source_binding'] == 'snapshot_and_live_match'
    assert lane['items'][0]['target_name'] == 'target'
    assert 'target' not in lane['items'][0], 'No invented indexed callee reference'
    brief = server.get_state_flow(file='Panel.ts', symbol='Outer', target_root=str(target), max_items=1)
    assert 'local_arrow_sibling_calls' in brief
    assert lane['value_transfer'] == lane['runtime_execution'] == 'not_established'


@pytest.mark.parametrize('damage', ['none', 'legacy', 'source_drift', 'corrupt_primary', 'packet_budget'])
def test_sibling_actual_source_primary_packet_refusal(sibling_atlas_run, tmp_path, monkeypatch, damage):
    from tools.core.artifact_store import ArtifactStore
    from tools.mcp import server
    import sqlite3
    target, _, original = sibling_atlas_run
    local = tmp_path / 'target'
    local.mkdir()
    (local / 'Panel.ts').write_bytes((target / 'Panel.ts').read_bytes())
    atlas = deepcopy(original)
    atlas['MAIN']['root_path'] = str(local)
    if damage == 'legacy':
        for row in atlas['MAIN']['files']['Panel.ts']['symbols']: row.pop(KEY, None)
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
    if damage == 'source_drift': (local / 'Panel.ts').write_bytes((local / 'Panel.ts').read_bytes() + b'\n// drift')
    if damage == 'packet_budget': monkeypatch.setattr(server, 'DEFAULT_AGENT_PACKET_BUDGET_TOKENS', 300)
    server._ATLAS_CACHE.clear()
    monkeypatch.setattr(server, '_raw_dir_for_target', lambda root: raw)
    monkeypatch.setattr(server, '_analysis_root_display', lambda root: str(local))
    result = json.loads(server.get_state_flow(file='Panel.ts', symbol='Outer', target_root=str(local), format='json'))
    lane = result.get('symbol_context', {}).get('local_arrow_sibling_calls')
    if damage == 'none': assert lane['status'] == 'observed' and lane['returned'] == 2
    elif damage in {'legacy', 'corrupt_primary', 'packet_budget'}:
        assert lane is None or lane['returned'] == 0
        if damage == 'packet_budget' and lane is None:
            notice = result['next_action'].lower()
            assert 'result' in notice and 'sibling' in notice and 'absence' in notice
    else: assert lane['status'] == 'unavailable' and lane['items'] == []


def test_sibling_uses_one_shared_coordinate_pass(sibling_inputs, monkeypatch):
    from tools.engines import generate_atlas
    source, raw, _ = sibling_inputs
    original = generate_atlas._utf16_offsets_to_utf8_byte_offsets
    calls = []
    def counted(content, offsets):
        calls.append(len(offsets))
        return original(content, offsets)
    monkeypatch.setattr(generate_atlas, '_utf16_offsets_to_utf8_byte_offsets', counted)
    generate_atlas._normalize_polyglot_symbols(raw, source, 'typescript')
    assert len(calls) == 1


def test_sibling_native_cap_and_degraded_are_not_empty_proof(tmp_path):
    body = 'const target = () => {}; const caller = () => {' + 'target();' * 70 + '};'
    path = tmp_path / 'Cap.ts'
    path.write_bytes(('export function Outer() {' + body + '}').encode('utf-8'))
    row = next(row for row in _native(path) if row['name'] == 'Outer')
    assert row[KEY]['status'] == 'incomplete_scan' and len(row[KEY]['records']) == 64 and row[KEY]['omitted'] == 6
    path.write_bytes(('export function Outer() {' + body + '}\nconst broken = ;').encode('utf-8'))
    row = next(row for row in _native(path) if row['name'] == 'Outer')
    assert row[KEY]['status'] == 'unavailable' and row[KEY]['records'] == []
