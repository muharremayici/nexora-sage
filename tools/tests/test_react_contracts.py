from tools.core.react_evidence import attach_react_evidence_contract


def test_react_evidence_contract_smoke():
    finding = attach_react_evidence_contract(
        {"file": "src/App.tsx", "score": 8},
        evidence_kinds={"static", "atlas_feature", "runtime_smoke_ready"},
    )

    assert finding["evidence_ladder"] == ["static_regex", "atlas_feature", "runtime_smoke_ready"]
    assert finding["runtime_proof_status"] == "runtime_smoke_ready"
    assert finding["confidence"] in {"likely", "confirmed"}
