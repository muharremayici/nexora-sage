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
    assert generate_atlas_module.safe_parse_success_record_valid is atlas_ast_contract.safe_parse_success_record_valid
    assert generate_atlas_module.SAFE_PARSE_SUCCESS_ROLES is atlas_ast_contract.SAFE_PARSE_SUCCESS_ROLES
    assert generate_atlas_module.EFFECT_SAFE_PARSE_ROLES is atlas_ast_contract.EFFECT_SAFE_PARSE_ROLES
    assert generate_atlas_module.effect_safe_parse_record_valid is atlas_ast_contract.effect_safe_parse_record_valid
    assert not atlas_ast_contract.effect_safe_parse_record_valid({}, lambda span: True)
    assert not atlas_ast_contract.safe_parse_success_record_valid({}, lambda span: True)
    from tools import inspect_target
    assert inspect_target.safe_parse_success_stored_problem is atlas_ast_contract.safe_parse_success_stored_problem
    assert atlas_ast_contract.safe_parse_success_stored_problem({}, "unknown") == "stored_contract_invalid"
    assert inspect_target.effect_safe_parse_stored_problem is atlas_ast_contract.effect_safe_parse_stored_problem
    assert atlas_ast_contract.effect_safe_parse_stored_problem({}, "unknown") == "stored_contract_invalid"
    tree = ast.parse(Path(atlas_ast_contract.__file__).read_text(encoding='utf-8'))
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))
    from tools.core import safeparse_early_return_contract as leaf
    assert generate_atlas_module.early_return_record_valid is leaf.early_return_record_valid
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(ast.parse(Path(leaf.__file__).read_text(encoding='utf-8'))))
    state_flow = ast.parse((Path(atlas_ast_contract.__file__).parent / 'state_flow.py').read_text(encoding='utf-8'))
    assert not any(isinstance(node, ast.ImportFrom) and node.module == 'tools.engines.generate_atlas'
                   for node in ast.walk(state_flow))


def test_wiring_observation_normalizer_has_one_owner_and_changes_staging_identity(monkeypatch):
    from tools.core import wiring_source_observations as owner
    assert generate_atlas_module.normalize_wiring_source_observations is owner.normalize_wiring_source_observations
    before = generate_atlas_module.atlas_staging_producer_contract()
    read_bytes = Path.read_bytes
    helper = Path(owner.__file__).resolve()
    def changed_helper_bytes(path):
        data = read_bytes(path)
        return data + b'\n# diagnostic content change' if path.resolve() == helper else data
    monkeypatch.setattr(Path, 'read_bytes', changed_helper_bytes)
    assert generate_atlas_module.atlas_staging_producer_contract() != before


def test_parameter_merge_contract_has_one_pure_owner_and_distinct_grammar():
    from tools.core import atlas_ast_contract as owner
    from tools import inspect_target as view
    assert generate_atlas_module.effect_safe_parse_merge_record_valid is owner.effect_safe_parse_merge_record_valid
    assert view.effect_safe_parse_merge_stored_problem is owner.effect_safe_parse_merge_stored_problem
    assert owner.EFFECT_SAFE_PARSE_MERGE_ROLES - owner.EFFECT_SAFE_PARSE_ROLES == {
        "prior_declaration", "prior_binding", "prior_spread", "prior_ref"}
    assert owner.EFFECT_SAFE_PARSE_MERGE_VERSION != owner.EFFECT_SAFE_PARSE_VERSION
    for row in (None, {}, {"spans": {}}):
        assert not owner.effect_safe_parse_merge_record_valid(row, lambda span: True)


def test_early_return_leaf_changes_staging_identity_and_refuses_malformed(monkeypatch):
    from tools.core import safeparse_early_return_contract as leaf
    assert len(leaf.EARLY_RETURN_ROLES) == 20
    for row in (None, {}, {"spans": {}}, {"spans": {}, "schema_validated": True}):
        assert not leaf.early_return_record_valid(row, lambda span: True)
    before, native = generate_atlas_module.atlas_staging_producer_contract(), Path.read_bytes
    def changed(path):
        value = native(path)
        return value + b'\n# diagnostic change' if path.resolve() == Path(leaf.__file__).resolve() else value
    monkeypatch.setattr(Path, 'read_bytes', changed)
    assert generate_atlas_module.atlas_staging_producer_contract() != before


def test_early_return_stored_guard_has_one_import_free_owner_and_explicit_ast_identity():
    from tools.core import safeparse_early_return_contract as owner
    from tools import inspect_target as view
    assert view.early_return_stored_problem is owner.early_return_stored_problem
    assert owner.early_return_stored_problem({}, "unknown", ast_contract_version=generate_atlas_module.AST_CONTRACT_VERSION) == "stored_contract_invalid"
    for identity in (None, "", 1, []):
        assert owner.early_return_stored_problem({}, "unknown", ast_contract_version=identity) == "stored_contract_invalid"


def test_factory_return_contract_has_one_pure_owner_and_staging_identity(monkeypatch):
    import ast
    from tools.core import factory_return_contract as owner
    assert generate_atlas_module.factory_return_record_valid is owner.factory_return_record_valid
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(ast.parse(Path(owner.__file__).read_text(encoding="utf-8"))))
    assert not owner.factory_return_record_valid({}, lambda span: True)
    before, read = generate_atlas_module.atlas_staging_producer_contract(), Path.read_bytes
    def changed(path):
        data = read(path)
        return data + b"\n# diagnostic" if path.resolve() == Path(owner.__file__).resolve() else data
    monkeypatch.setattr(Path, "read_bytes", changed)
    assert generate_atlas_module.atlas_staging_producer_contract() != before


def test_factory_return_stored_guard_uses_pure_owner_and_explicit_current_ast():
    from tools.core import factory_return_contract as owner
    from tools import inspect_target
    assert inspect_target.factory_return_stored_problem is owner.factory_return_stored_problem
    for identity in (None, "", 1, []):
        assert owner.factory_return_stored_problem({}, "unknown", ast_contract_version=identity) == "stored_contract_invalid"


def test_factory_argument_guard_has_same_import_free_owner():
    from tools.core import factory_return_contract as owner
    assert generate_atlas_module.factory_argument_record_valid is owner.factory_argument_record_valid
    assert generate_atlas_module.factory_argument_parent_valid is owner.factory_argument_parent_valid
    for row in (None, {}, {"argument_count": True}, {"argument_count": 5}, {"spans": {}}):
        assert not owner.factory_argument_record_valid(row, lambda span: True)


def test_factory_argument_stored_guard_has_pure_owner_and_explicit_ast():
    from tools.core import factory_return_contract as owner
    from tools import inspect_target as view
    assert view.factory_argument_stored_problem is owner.factory_argument_stored_problem
    for identity in (None,"",1,[]):
        assert owner.factory_argument_stored_problem({}, {}, "unknown",ast_contract_version=identity) == "stored_contract_invalid"


@pytest.mark.parametrize('name', _SHARED_AST_RECORD_CHECKS)
@pytest.mark.parametrize('row', [None, {}])
def test_shared_ast_record_checks_keep_malformed_records_refused(name, row):
    from tools.core import atlas_ast_contract
    outer = {'start': 0, 'end': 100, 'line': 1, 'end_line': 10}
    assert getattr(atlas_ast_contract, name)(row, outer, lambda span: True) is False
