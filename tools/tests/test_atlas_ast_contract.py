"""Pure shared AST ownership and malformed-record compatibility controls."""

from pathlib import Path

import pytest

from tools.engines import generate_atlas as generate_atlas_module


_SHARED_AST_RECORD_CHECKS = [
    '_jsx_callback_record_valid', '_call_result_property_binding_record_valid',
    '_returned_local_callback_record_valid', '_local_arrow_sibling_record_valid',
    '_local_arrow_result_use_record_valid',
    '_call_result_alias_invocation_record_valid', '_direct_optional_alias_record_valid',
    '_nested_callback_alias_record_valid', '_optional_rest_alias_record_valid',
    '_parameter_config_callback_record_valid', '_returned_parameter_optional_record_valid',
    '_returned_parameter_context_record_valid',
]


def test_atlas_ast_contract_has_one_pure_owner_and_compatible_producer_exports():
    import ast
    from tools.core import atlas_ast_contract
    assert generate_atlas_module.AST_CONTRACT_VERSION == atlas_ast_contract.AST_CONTRACT_VERSION
    for name in [*_SHARED_AST_RECORD_CHECKS, '_returned_parameter_anchor', '_returned_parameter_context_roles']:
        assert getattr(generate_atlas_module, name) is getattr(atlas_ast_contract, name)
        assert getattr(atlas_ast_contract, name).__module__ == 'tools.core.atlas_ast_contract'
    tree = ast.parse(Path(atlas_ast_contract.__file__).read_text(encoding='utf-8'))
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))
    state_flow = ast.parse((Path(atlas_ast_contract.__file__).parent / 'state_flow.py').read_text(encoding='utf-8'))
    assert not any(isinstance(node, ast.ImportFrom) and node.module == 'tools.engines.generate_atlas'
                   for node in ast.walk(state_flow))


@pytest.mark.parametrize('name', _SHARED_AST_RECORD_CHECKS)
@pytest.mark.parametrize('row', [None, {}])
def test_shared_ast_record_checks_keep_malformed_records_refused(name, row):
    from tools.core import atlas_ast_contract
    outer = {'start': 0, 'end': 100, 'line': 1, 'end_line': 10}
    assert getattr(atlas_ast_contract, name)(row, outer, lambda span: True) is False
