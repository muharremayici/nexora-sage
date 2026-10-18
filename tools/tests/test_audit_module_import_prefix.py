"""Declared import-prefix admission across actual Audit evidence consumers."""
import json
import subprocess

import pytest

from tools.core import contextos_mcp
from tools.core.atlas_io import load_atlas_data_with_identity
from tools.core.polyglot_imports import extract_typescript_import_evidence
from tools.engines import audit
from tools.mcp import server
from tools.tests.test_audit_import_subject_evidence import ROOT, _fixture


@pytest.mark.parametrize('alias,container,raw', [
    ('@/', 'modules', 'pkg/@/modules/b/private'),
    ('@/', 'modules', 'pkg/@/modules/a/api'),
    ('@/', 'features/catalog', 'pkg/@/features/catalog/b/private'),
    ('~/', 'features/catalog', 'external/~/features/catalog/b/private'),
    ('#app/', 'modules', 'external/#app/modules/b/private'),
])
def test_module_import_prefix_must_start_raw_specifier(alias, container, raw):
    from tools.core.import_classifier import module_source_context, module_api_import_evidence
    decision = module_api_import_evidence(module_source_context(f'{container}/a/Panel.ts', container), raw,
        module_container=container, primary_alias=alias)
    assert decision['violated'] is False
    assert not decision.get('api_entry_policy_required', False)
    assert decision['trace']['raw_prefix_offset'] is None
    assert decision['trace']['imported_module'] is None


@pytest.mark.parametrize('alias,container', [('@/', 'modules'), ('~/', 'features/catalog'), ('#app/', 'modules')])
def test_declared_module_import_prefix_still_emits_real_deep_import(alias, container):
    from tools.core.import_classifier import module_source_context, module_api_import_evidence
    raw = f'{alias}{container}/b/private'
    decision = module_api_import_evidence(module_source_context(f'{container}/a/Panel.ts', container), raw,
        module_container=container, primary_alias=alias)
    assert decision['violated'] is True
    assert decision['trace']['raw_prefix_offset'] == 0
    assert decision['trace']['imported_module'] == 'b'


@pytest.fixture(scope='module')
def embedded_prefix_native_imports(tmp_path_factory):
    directory = tmp_path_factory.mktemp('audit-import-prefix')
    content = """// Literal source evidence, not a framework or runtime verdict.
import { Good } from '@/features/catalog/b/private';
import { Own } from '@/features/catalog/a/api';
import { Foreign } from 'pkg/@/features/catalog/b/private';
import { ForeignOwn } from 'pkg/@/features/catalog/a/api';
export const value = { Good, Own, Foreign, ForeignOwn };
"""
    files = [directory / f'Prefix.{ext}' for ext in ('ts', 'js')]
    for file in files:
        file.write_text(content, encoding='utf-8')
    result = subprocess.run(['node', str(ROOT / 'tools/engines/ast_sequencer.cjs'),
        '--batch-json', *map(str, files)], capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    return content, {file.suffix: extract_typescript_import_evidence(parsed[str(file)]) for file in files}


@pytest.mark.parametrize('ext', ['ts', 'js'])
def test_embedded_module_prefix_actual_audit_and_three_consumers(tmp_path, monkeypatch,
                                                               embedded_prefix_native_imports, ext):
    content, imports = embedded_prefix_native_imports
    monkeypatch.setattr(audit, 'get_module_root_name', lambda: 'features/catalog')
    rel = f'features/catalog/a/Prefix.{ext}'
    data = _fixture(tmp_path, imports[f'.{ext}'], monkeypatch,
        rel_path=rel, content=content, profile='FSD_STANDARD')
    rows = [row for row in data['report']['violations'] if row['rule'] in ('deep_imports', 'own_api_imports')]
    assert len(rows) == 2
    assert {row['rule'] for row in rows} == {'deep_imports', 'own_api_imports'}
    assert all('pkg/@/' not in row['detail'] for row in rows)
    items, total, _ = server._audit_violation_work_items_from_sqlite(data['raw'], page=1, page_size=20, project='MAIN')
    integrity, itotal, _ = server._module_integrity_items_from_sqlite(data['raw'], f'MAIN::{rel}',
        max_items=20, target_root=str(data['root']))
    directives = contextos_mcp.build_agent_action_directives(audit_report={'violations': rows},
        raw_dir=data['raw'], _atlas_read=load_atlas_data_with_identity(data['raw']), max_items=20)
    assert total == itotal == 2
    for consumer in (items, integrity, directives):
        assert {row['rule'] for row in consumer} == {'deep_imports', 'own_api_imports'}
        for projected in consumer:
            expected = next(row for row in rows if row['rule'] == projected['rule'])
            assert projected['finding_evidence'] == expected['finding_evidence']
            assert projected['finding_evidence']['module_trace']['raw_prefix_offset'] == 0
            assert projected['finding_evidence']['status'] == 'bound_record_set'
