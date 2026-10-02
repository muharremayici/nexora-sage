import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.core.react_evidence import attach_react_evidence_contract
from tools.engines.react_runtime_intelligence import (
    _calibrate_with_ecosystem,
    analyze_runtime_intelligence_file,
)
from tools.engines.react_compiler_readiness import _normalize_finding
from tools.validate_react_v11_contracts import _taxonomy_check


def test_runtime_schema_mutation_fixture_requires_atlas_evidence():
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "config"
        / "target_runtime_schema_mutation_advisory_fixtures.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = fixture["cases"]

    assert fixture["meta"]["status"] == "syntax_ast_advisory_calibration"
    assert len({case["id"] for case in cases}) == len(cases)
    assert {case["expected"] for case in cases} == {
        "advisory_candidate",
        "guarded",
        "intentional_opaque",
        "requires_cross_file_resolution",
        "producer_only_insufficient_sink",
    }
    for case in cases:
        assert case["source"] and case["required_evidence"] and case["claim_ceiling"]
        assert case["engine_expectation"] in {"advisory", "none", "out_of_scope"}
        result = analyze_runtime_intelligence_file("fixture", f"{case['id']}.tsx", case["source"])
        assert result is not None
        assert not any(
            finding["dimension"] == "target_runtime_schema_mutation"
            for finding in result["findings"]
        ), "Source text alone must not trigger the syntax-AST advisory"


@pytest.mark.parametrize(
    "case_id",
    [
        "direct_json_cast_to_structured_state",
        "schema_parse_before_structured_state",
        "dominating_parse_before_explicit_cast",
        "intentionally_opaque_response_state",
        "generic_json_adapter_without_state_sink",
        "local_fetch_shadow_is_not_external_response",
        "corpus_unrelated_response_and_state_cast",
    ],
)
def test_runtime_response_state_cast_uses_syntax_ast_evidence(case_id, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads(
        (root / "config" / "target_runtime_schema_mutation_advisory_fixtures.json").read_text(
            encoding="utf-8"
        )
    )
    case = next(item for item in fixture["cases"] if item["id"] == case_id)
    target = tmp_path / f"{case_id}.tsx"
    target.write_text(case["source"], encoding="utf-8")
    sequencer = root / "tools" / "engines" / "ast_sequencer.cjs"
    result = subprocess.run(
        [node, str(sequencer), str(target)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    symbols = json.loads(result.stdout)
    meta = next(item for item in symbols if item["name"] == "__file_meta__")
    assert meta["parserStatus"] == "observed"
    feature_prefix = "React:ExternalResponseStateCast:"
    features = [feature for feature in meta["features"] if feature.startswith(feature_prefix)]
    atlas_file = {
        "symbols": [symbol for symbol in symbols if symbol["name"] != "__file_meta__"],
        "features": meta["features"],
    }
    assert all(symbol["name"] != "__file_meta__" for symbol in atlas_file["symbols"])
    analysis = analyze_runtime_intelligence_file(
        "fixture",
        target.name,
        case["source"],
        atlas_file=atlas_file,
    )
    findings = [
        item for item in analysis["findings"]
        if item["dimension"] == "target_runtime_schema_mutation"
    ]
    if case["engine_expectation"] == "advisory":
        assert len(features) == len(findings) == 1
        assert findings[0]["confidence"] == "needs_runtime_proof"
        assert findings[0]["calibration_lane"] == "needs_runtime_probe"
        assert len(findings[0]["evidence_spans"]) == 2
    else:
        assert not features
        assert not findings


@pytest.mark.parametrize(
    "case_id",
    [
        "explicit_any_json_to_structured_state",
        "explicit_any_json_dominating_parse",
        "explicit_any_json_to_opaque_state",
        "explicit_any_json_from_shadowed_fetch",
    ],
)
def test_runtime_explicit_any_response_state_advisory(case_id, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads(
        (root / "config" / "target_runtime_schema_mutation_advisory_fixtures.json").read_text(
            encoding="utf-8"
        )
    )
    case = next(item for item in fixture["cases"] if item["id"] == case_id)
    target = tmp_path / f"{case_id}.tsx"
    target.write_text(case["source"], encoding="utf-8")
    result = subprocess.run(
        [node, str(root / "tools" / "engines" / "ast_sequencer.cjs"), str(target)],
        cwd=root, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    symbols = json.loads(result.stdout)
    meta = next(item for item in symbols if item["name"] == "__file_meta__")
    assert meta["parserStatus"] == "observed"
    features = [
        feature for feature in meta["features"]
        if feature.startswith("React:ExternalResponseAnyState:")
    ]
    atlas_file = {
        "symbols": [symbol for symbol in symbols if symbol["name"] != "__file_meta__"],
        "features": meta["features"],
    }
    analysis = analyze_runtime_intelligence_file(
        "fixture", target.name, case["source"], atlas_file=atlas_file,
    )
    findings = [
        item for item in analysis["findings"]
        if item["risk"] == "external_response_any_to_typed_react_state"
    ]
    if case["engine_expectation"] == "advisory":
        assert len(features) == len(findings) == 1
        assert findings[0]["confidence"] == "needs_runtime_proof"
        assert findings[0]["calibration_lane"] == "needs_runtime_probe"
        assert len(findings[0]["evidence_spans"]) == 2
    else:
        assert not features
        assert not findings


@pytest.mark.parametrize(
    "case_id",
    [
        "unannotated_json_to_typed_state",
        "inline_json_to_typed_state",
        "unannotated_json_dominating_parse",
        "unannotated_json_assigned_parse_result",
        "unannotated_json_safe_parse_branch",
        "unannotated_json_to_opaque_state",
        "unannotated_json_from_local_fetch",
        "inline_json_before_fetch_response_declaration",
    ],
)
def test_runtime_direct_json_response_state_advisory(case_id, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads(
        (root / "config" / "target_runtime_schema_mutation_advisory_fixtures.json").read_text(
            encoding="utf-8"
        )
    )
    case = next(item for item in fixture["cases"] if item["id"] == case_id)
    target = tmp_path / f"{case_id}.tsx"
    target.write_text(case["source"], encoding="utf-8")
    result = subprocess.run(
        [node, str(root / "tools" / "engines" / "ast_sequencer.cjs"), str(target)],
        cwd=root, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    symbols = json.loads(result.stdout)
    meta = next(item for item in symbols if item["name"] == "__file_meta__")
    assert meta["parserStatus"] == "observed"
    features = [
        feature for feature in meta["features"]
        if feature.startswith("React:ExternalResponseDirectState:")
    ]
    analysis = analyze_runtime_intelligence_file(
        "fixture", target.name, case["source"],
        atlas_file={
            "symbols": [symbol for symbol in symbols if symbol["name"] != "__file_meta__"],
            "features": meta["features"],
        },
    )
    findings = [
        item for item in analysis["findings"]
        if item["risk"] == "external_response_direct_json_to_typed_react_state"
    ]
    if case["engine_expectation"] == "advisory":
        assert len(features) == len(findings) == 1
        assert findings[0]["confidence"] == "needs_runtime_proof"
        assert findings[0]["calibration_lane"] == "needs_runtime_probe"
        assert len(findings[0]["evidence_spans"]) == 2
    else:
        assert not features
        assert not findings


def test_runtime_response_state_cast_ignores_degraded_parser(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads(
        (root / "config" / "target_runtime_schema_mutation_advisory_fixtures.json").read_text(
            encoding="utf-8"
        )
    )
    source = next(
        item["source"] for item in fixture["cases"]
        if item["id"] == "direct_json_cast_to_structured_state"
    )
    target = tmp_path / "malformed.tsx"
    target.write_text(source + "\nconst = ;", encoding="utf-8")
    result = subprocess.run(
        [node, str(root / "tools" / "engines" / "ast_sequencer.cjs"), str(target)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    meta = next(item for item in json.loads(result.stdout) if item["name"] == "__file_meta__")
    assert meta["parserStatus"] == "degraded"
    assert not any(
        feature.startswith((
            "React:ExternalResponseStateCast:",
            "React:ExternalResponseAnyState:",
            "React:ExternalResponseDirectState:",
        ))
        for feature in meta["features"]
    )


@pytest.mark.parametrize(
    ("source", "feature"),
    [
        ("", "React:ExternalResponseStateCast:4:2"),
        ("import { useState } from 'react';", "React:ExternalResponseStateCast:40:20"),
        ("", "React:ExternalResponseAnyState:4:2"),
        ("import { useState } from 'react';", "React:ExternalResponseAnyState:40:20"),
        ("", "React:ExternalResponseDirectState:4:2"),
        ("import { useState } from 'react';", "React:ExternalResponseDirectState:40:20"),
        ("import { useState } from 'react';", "React:ExternalResponseDirectState:1:1"),
    ],
)
def test_runtime_response_state_cast_rejects_unbound_source_spans(source, feature):
    result = analyze_runtime_intelligence_file(
        "fixture",
        "Items.tsx",
        source,
        atlas_file={"features": [feature], "symbols": []},
    )
    assert result is not None
    assert not any(
        finding["dimension"] == "target_runtime_schema_mutation"
        for finding in result["findings"]
    )


@pytest.mark.parametrize(
    ("feature", "expected"),
    [
        ("React:MutableAssignment:render:3", True),
        ("React:MutableAssignment:unresolved:3", True),
        ("React:MutableAssignment:event:3", False),
        ("React:MutableAssignment:render:30", False),
    ],
)
def test_compiler_mutation_consumes_real_atlas_file_features(feature, expected):
    source = "export function Panel(props) {\n  const value = props.value;\n  props.value = value + 1;\n  return <div>{value}</div>;\n}"
    row = analyze_runtime_intelligence_file(
        "fixture", "src/Panel.tsx", source,
        atlas_file={"features": [feature], "symbols": []},
    )
    findings = [item for item in row["findings"] if item["risk"] == "compiler_static_contract_risk"]
    assert bool(findings) is expected
    if expected:
        finding = findings[0]
        assert finding["line"] == 3
        assert finding["confidence"] != "confirmed"
        assert finding["runtime_proof_status"] != "runtime_confirmed"
        assert "react_compiler" not in finding["evidence_kinds"]
        assert _normalize_finding(finding, "react_runtime_intelligence")["readiness_status"] == "review"


def test_compiler_hook_consumes_file_features_without_native_diagnostic_claim():
    source = "import { useEffect } from 'react';\nexport function Panel() {\n  useEffect(() => {});\n  return <div />;\n}"
    row = analyze_runtime_intelligence_file(
        "fixture", "src/Panel.tsx", source,
        atlas_file={"features": ["Hook:MissingDeps:useEffect"], "symbols": []},
    )
    finding = next(item for item in row["findings"] if item["risk"] == "compiler_static_contract_risk")
    assert finding["confidence"] != "confirmed"
    assert finding["runtime_proof_status"] != "runtime_confirmed"
    assert finding["line"] == 3
    assert _normalize_finding(finding, "react_runtime_intelligence")["readiness_status"] == "review"


def test_compiler_legacy_hook_feature_rejects_ambiguous_source_line():
    source = "import { useEffect } from 'react';\nexport function Panel() {\n  useEffect(() => {});\n  useEffect(() => {});\n  return <div />;\n}"
    row = analyze_runtime_intelligence_file(
        "fixture", "src/Panel.tsx", source,
        atlas_file={"features": ["Hook:MissingDeps:useEffect"], "symbols": []},
    )
    assert not any(item["risk"] == "compiler_static_contract_risk" for item in row["findings"])


def test_combined_static_hook_and_mutation_stays_review_only():
    source = "import { useEffect } from 'react';\nexport function Panel(props) {\n  useEffect(() => {});\n  props.value = 1;\n  return <div />;\n}"
    row = analyze_runtime_intelligence_file(
        "fixture", "src/Panel.tsx", source,
        atlas_file={
            "features": [
                "Hook:MissingDeps:useEffect:3",
                "React:MutableAssignment:render:4",
            ],
            "symbols": [],
        },
    )
    finding = next(item for item in row["findings"] if item["risk"] == "compiler_static_contract_risk")
    assert finding["score"] == 10
    assert finding["confidence"] == "needs_runtime_proof"
    assert finding["calibration_lane"] == "needs_runtime_probe"
    assert [span["line"] for span in finding["evidence_spans"]] == [3, 4]
    assert _normalize_finding(finding, "react_runtime_intelligence")["readiness_status"] == "review"
    corroborated = _calibrate_with_ecosystem(
        [finding],
        [{"project": "fixture", "file": "src/Panel.tsx", "dimension": "hook_contract"}],
    )[0]
    assert corroborated["confidence"] == "needs_runtime_proof"
    assert corroborated["calibration_lane"] == "needs_runtime_probe"


@pytest.mark.parametrize(
    ("source", "expected_feature"),
    [
        ("import { useEffect } from 'react';\nexport function Panel() {\n  useEffect(() => {});\n  return <div />;\n}", "Hook:MissingDeps:useEffect:3"),
        ("import { useEffect as effect } from 'react';\nexport function Panel() {\n  effect(() => {}, deps);\n  return <div />;\n}", "Hook:DynamicDeps:useEffect:3"),
        ("import * as React from 'react';\nexport function Panel() {\n  React.useEffect(() => {});\n  return <div />;\n}", "Hook:MissingDeps:useEffect:3"),
        ("import React from 'react';\nexport function Panel() {\n  React.useEffect(() => {});\n  return <div />;\n}", "Hook:MissingDeps:useEffect:3"),
        ("import { useEffect } from 'other';\nexport function Panel() {\n  useEffect(() => {});\n  return <div />;\n}", None),
        ("import { useEffect } from 'react';\nexport function Panel() {\n  const useEffect = (fn) => fn();\n  useEffect(() => {});\n  return <div />;\n}", None),
    ],
)
def test_compiler_hook_ast_feature_is_react_import_bound(source, expected_feature, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / "Panel.tsx"
    target.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [node, str(root / "tools" / "engines" / "ast_sequencer.cjs"), str(target)],
        cwd=root, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    meta = next(item for item in json.loads(result.stdout) if item["name"] == "__file_meta__")
    features = [item for item in meta["features"] if item.startswith(("Hook:MissingDeps:", "Hook:DynamicDeps:"))]
    if expected_feature:
        assert expected_feature in features
        analysis = analyze_runtime_intelligence_file(
            "fixture", "src/Panel.tsx", source,
            atlas_file={"features": meta["features"], "symbols": []},
        )
        finding = next(item for item in analysis["findings"] if item["risk"] == "compiler_static_contract_risk")
        assert finding["line"] == 3
        assert finding["confidence"] != "confirmed"
    else:
        assert not features


def test_compiler_hook_ast_skips_degraded_parser(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / "Panel.tsx"
    target.write_text(
        "import { useEffect } from 'react';\n"
        "export function Panel() { useEffect(() => {}); return <div />; }\n"
        "const = ;\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(root / "tools" / "engines" / "ast_sequencer.cjs"), str(target)],
        cwd=root, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    meta = next(item for item in json.loads(result.stdout) if item["name"] == "__file_meta__")
    assert meta["parserStatus"] == "degraded"
    assert not any(item.startswith("Hook:MissingDeps:") for item in meta["features"])


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
