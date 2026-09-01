from tools.core.react_evidence import attach_react_evidence_contract
from tools.validate_react_v11_contracts import _taxonomy_check


def test_react_evidence_contract_smoke():
    finding = attach_react_evidence_contract(
        {"file": "src/App.tsx", "score": 8},
        evidence_kinds={"static", "atlas_feature", "runtime_smoke_ready"},
    )

    assert finding["evidence_ladder"] == ["static_regex", "atlas_feature", "runtime_smoke_ready"]
    assert finding["runtime_proof_status"] == "runtime_smoke_ready"
    assert finding["confidence"] in {"likely", "confirmed"}


def test_react_v11_taxonomy_uses_declared_evidence_releases():
    result = _taxonomy_check()

    assert result["included_releases"] == ["1.0.0"]
    assert not result["missing"]
    assert not result["not_proven"]
    assert not result["unlinked"]
    assert not result["undeclared_linked"]
