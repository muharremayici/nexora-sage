from tools.engines.quality_gate import _structural_contract_coverage


RICH_MEMBER = {
    "dependencies": [],
    "dependencyImports": [],
    "uiDependencies": [],
    "dynamicImports": [],
    "architecturalMarkers": [],
    "sideEffectMarkers": [],
    "sideEffectImports": [],
    "sideEffectCalls": [],
}


def _atlas_with_symbols(*symbols):
    return {
        "MAIN": {
            "files": {
                "sample": {
                    "ast_contract_version": "v18.5-form-binding-evidence",
                    "symbols": list(symbols),
                }
            }
        }
    }


def test_python_members_are_observed_but_not_forced_through_typescript_rich_contract():
    atlas = _atlas_with_symbols(
        {
            "normalization_profile": "python_ast_v1",
            "member_details": [{"name": "run", "dependencies": []}],
        }
    )

    coverage = _structural_contract_coverage(atlas=atlas, genome={})

    assert coverage["totals"]["observed_member_details"] == 1
    assert coverage["totals"]["member_details"] == 0
    assert coverage["totals"]["excluded_member_details"] == 1
    assert coverage["member_detail_applicability"]["excluded_by_profile"] == {
        "python_ast_v1": 1
    }


def test_required_profile_remains_subject_to_rich_member_contract():
    atlas = _atlas_with_symbols(
        {
            "normalization_profile": "typescript_react_v1",
            "member_details": [RICH_MEMBER, {"dependencies": []}],
        }
    )

    coverage = _structural_contract_coverage(atlas=atlas, genome={})

    assert coverage["totals"]["observed_member_details"] == 2
    assert coverage["totals"]["member_details"] == 2
    assert coverage["member_detail_contract_ratio"] == 0.5


def test_undeclared_profile_cannot_escape_rich_member_contract():
    atlas = _atlas_with_symbols(
        {
            "normalization_profile": "future_parser_v1",
            "member_details": [{"dependencies": []}],
        }
    )

    coverage = _structural_contract_coverage(atlas=atlas, genome={})

    assert coverage["totals"]["member_details"] == 1
    assert coverage["member_detail_contract_ratio"] == 0.0
    assert coverage["member_detail_applicability"]["unknown_profiles_treated_as_required"] == {
        "future_parser_v1": 1
    }
