import json
import os
import shutil
import subprocess
import sys
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


@pytest.mark.parametrize(
    "case", json.loads((Path(__file__).resolve().parents[2] / "config" /
        "target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))["field_state_cases"],
    ids=lambda case: case["id"],
)
def test_runtime_response_field_advisory_requires_exact_source_binding(case, tmp_path):
    node = shutil.which("node")
    assert node, "Node is required for this syntax-AST contract"
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / "Panel.tsx"
    target.write_text(case["source"], encoding="utf-8", newline="")
    result = subprocess.run(
        [node, str(root / "tools/engines/ast_sequencer.cjs"), str(target)],
        cwd=root, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    symbols = json.loads(result.stdout)
    meta = next(item for item in symbols if item["name"] == "__file_meta__")
    assert meta["parserStatus"] == "observed"
    features = [item for item in meta["features"] if item.startswith("React:ExternalResponseFieldState:")]
    from tools.engines.generate_atlas import _response_field_evidence
    evidence = _response_field_evidence(symbols, case["source"])
    assert evidence["status"] == "observed", evidence
    assert {row["feature"] for row in evidence["records"]} == set(features)
    assert evidence["scope"] == "existing_response_field_state_candidates"
    assert evidence["offset_unit"] == "utf8_bytes"
    encoded_source = case["source"].encode("utf-8")
    for row in evidence["records"]:
        assert row["field_path"]
        spans = row["spans"]
        def captured(role):
            span = spans[role]
            return encoded_source[span["start"]:span["end"]].decode("utf-8")
        assert "fetch(" in captured("fetch") and ".json(" in captured("json")
        assert captured("argument") in captured("sink")
        assert captured("json_binding") in captured("json")
        assert captured("setter_binding") in captured("sink")
    atlas_file = {"features": meta["features"],
        "symbols": [item for item in symbols if item["name"] != "__file_meta__"]}
    analysis = analyze_runtime_intelligence_file("fixture", target.name, case["source"], atlas_file=atlas_file)
    findings = [item for item in analysis["findings"]
        if item["risk"] == "external_response_field_to_typed_react_state"]
    assert bool(features) == bool(findings) == (case["expected"] == "advisory")
    if findings:
        assert len(features) == len(findings) == 1
        row = findings[0]
        assert row["confidence"] == "needs_runtime_proof"
        assert row["calibration_lane"] == "needs_runtime_probe"
        assert row["evidence_scope"] == "same_file_json_field_candidate"
        assert "upstream" in row["evidence"] and "not established" in row["evidence"]
        assert len(row["evidence_spans"]) == 2
    source_only = analyze_runtime_intelligence_file("fixture", target.name, case["source"])
    assert not any(item["dimension"] == "target_runtime_schema_mutation" for item in source_only["findings"])


@pytest.mark.parametrize("section,case_id", [
    ("cases", "direct_json_cast_to_structured_state"),
    ("field_state_cases", "direct_field"),
])
def test_runtime_response_state_cast_ignores_degraded_parser(section, case_id, tmp_path):
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
        item["source"] for item in fixture[section]
        if item["id"] == case_id
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
            "React:ExternalResponseFieldState:",
        ))
        for feature in meta["features"]
    )


def test_runtime_response_field_survives_real_atlas_sqlite_projection(tmp_path, monkeypatch):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug

    root = Path(__file__).resolve().parents[2]
    fixture = json.loads((root / "config" /
        "target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))
    source = next(case["source"] for case in fixture["field_state_cases"]
        if case["id"] == "branch_literal_fallback")
    target = tmp_path / "target"
    target.mkdir()
    (target / "Panel.tsx").write_text(source, encoding="utf-8")
    env = dict(os.environ)
    for key in ("CODEMAPS_TARGET_PREFLIGHT_RECEIPT", "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256",
            "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE", "CODEMAPS_TARGET_PROJECTS"):
        env.pop(key, None)
    env["CODEMAPS_TARGET_ROOT"] = str(target)
    result = subprocess.run([sys.executable, "-B", "-m", "tools.engines.generate_atlas"],
        cwd=root, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    raw = root / "output/external_targets" / external_target_output_slug(str(target)) / ".raw"
    assert (raw / "codemaps.db").is_file(), "Actual SQLite Atlas delivery is required"
    import tools.core.json_io as json_io
    native_json_read = json_io.load_json_file
    def reject_atlas_shadow(path, *args, **kwargs):
        assert Path(path) != raw / "atlas.json", "This test requires SQLite primary, not a JSON shadow fallback"
        return native_json_read(path, *args, **kwargs)
    monkeypatch.setattr(json_io, "load_json_file", reject_atlas_shadow)
    atlas = load_atlas_data(raw)
    file_data = atlas["MAIN"]["files"]["Panel.tsx"]
    from tools.engines.generate_atlas import file_contract_is_current
    assert file_contract_is_current(file_data)
    legacy_file = {**file_data, "ast_contract_version": "v18.29-repository-collection-read-return-review"}
    assert not file_contract_is_current(legacy_file), "Pre-field-feature Atlas evidence must be refreshed"
    assert not file_contract_is_current({**file_data, "ast_contract_version": "v18.31-shallow-nested-index-review"}), "Pre-transport evidence must be refreshed"
    assert file_data["parser_evidence"]["status"] == "observed"
    assert any(feature.startswith("React:ExternalResponseFieldState:") for feature in file_data["features"])
    evidence = file_data["response_field_evidence"]
    assert evidence["status"] == "observed" and len(evidence["records"]) == 1
    assert evidence["source_hash"] == file_data["hash"]
    assert evidence["ast_contract_version"] == file_data["ast_contract_version"]
    row = evidence["records"][0]
    assert row["field_path"] == ["image"]
    assert row["feature"] in file_data["features"]
    analysis = analyze_runtime_intelligence_file("MAIN", "Panel.tsx", source, atlas_file=file_data)
    rows = [row for row in analysis["findings"] if row["risk"] == "external_response_field_to_typed_react_state"]
    assert len(rows) == 1 and rows[0]["confidence"] == "needs_runtime_proof"
    assert rows[0]["evidence_scope"] == "same_file_json_field_candidate"


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r", "\u2028"])
def test_response_field_transport_preserves_unicode_spans_and_same_line_sinks(newline, tmp_path):
    from tools.engines.generate_atlas import _response_field_evidence
    node = shutil.which("node")
    assert node, "Node is required for this syntax-AST contract"
    root = Path(__file__).resolve().parents[2]
    source = ("\ufeff// \U0001f680 source identity and UTF-16 offsets\r\nimport {useState} from 'react';\r\n"
        "export async function Panel(){\r\n const [x,setX]=useState<string>('');\r\n"
        " const response=await fetch('/api');\r\n const json=await response.json();\r\n"
        " setX(json?.items[0]['name'] ?? ''); setX(json.image || '');\r\n}\r\n").replace("\r\n", newline)
    target = tmp_path / "Panel.tsx"
    target.write_bytes(source.encode("utf-8"))
    result = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(target)],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    symbols = json.loads(result.stdout)
    meta = next(row for row in symbols if row["name"] == "__file_meta__")
    evidence = _response_field_evidence(symbols, source)
    assert evidence["status"] == "observed"
    assert len(evidence["records"]) == 2
    assert [row["field_path"] for row in evidence["records"]] == [["items", "0", "name"], ["image"]]
    assert len({row["spans"]["sink"]["start"] for row in evidence["records"]}) == 2
    assert meta["responseFieldEvidence"]["offset_unit"] == "utf16_code_units"
    assert evidence["offset_unit"] == "utf8_bytes"
    encoded = source.encode("utf-8")
    for row in evidence["records"]:
        span = row["spans"]["sink"]
        assert encoded[span["start"]:span["end"]].decode("utf-8").startswith("setX(")
        assert span["start"] > source.index("setX(json"), "BOM/emoji byte positions must not equal Python/native offsets"
    assert len({row["feature"] for row in evidence["records"]}) == 1, "Legacy line-key feature membership stays unchanged"
    analysis = analyze_runtime_intelligence_file("fixture", target.name, source,
        atlas_file={"features": meta["features"], "symbols": [], "response_field_evidence": evidence})
    legacy = analyze_runtime_intelligence_file("fixture", target.name, source,
        atlas_file={"features": meta["features"], "symbols": []})
    assert analysis == legacy, "Transport must not add findings or promote confidence"


@pytest.mark.parametrize("damage", ["missing", "version", "degraded", "hash", "path", "boolean_offset",
    "out_of_bounds", "line", "feature", "binding_span", "unknown_claim", "missing_record", "duplicate_sink", "offset_unit", "atlas_hash"])
def test_response_field_transport_fails_closed_on_invalid_metadata(damage):
    from copy import deepcopy
    import hashlib
    from tools.engines.generate_atlas import _response_field_evidence
    source = "fetch\njson\nsink"
    def span(start, end, line):
        return {"start": start, "end": end, "line": line, "end_line": line}
    evidence = {"status": "observed", "version": "v1-literal-response-field-state",
        "offset_unit": "utf16_code_units", "source_hash": hashlib.sha256(source.encode()).hexdigest(),
        "records": [{"feature": "React:ExternalResponseFieldState:3:2", "field_path": ["image"],
            "spans": {"fetch": span(0, 5, 1), "json": span(6, 10, 2), "sink": span(11, 15, 3),
                "argument": span(12, 14, 3), "json_binding": span(6, 9, 2), "setter_binding": span(0, 4, 1)}}]}
    meta = {"name": "__file_meta__", "parserStatus": "observed", "responseFieldEvidence": deepcopy(evidence),
        "features": ["React:ExternalResponseFieldState:3:2", f"Hash:{evidence['source_hash']}"]}
    assert _response_field_evidence([meta], source)["status"] == "observed"
    row = meta["responseFieldEvidence"]["records"][0]
    if damage == "missing":
        meta.pop("responseFieldEvidence")
    elif damage == "version":
        meta["responseFieldEvidence"]["version"] = "future-unverified"
    elif damage == "degraded":
        meta["parserStatus"] = "degraded"
    elif damage == "hash":
        meta["responseFieldEvidence"]["source_hash"] = "0" * 64
    elif damage == "path":
        row["field_path"] = []
    elif damage == "boolean_offset":
        row["spans"]["sink"]["start"] = True
    elif damage == "out_of_bounds":
        row["spans"]["sink"]["end"] = 100
    elif damage == "line":
        row["spans"]["sink"]["line"] = 4
    elif damage == "feature":
        meta["features"] = []
    elif damage == "binding_span":
        row["spans"]["json_binding"] = row["spans"]["fetch"]
    elif damage == "missing_record":
        meta["responseFieldEvidence"]["records"] = []
    elif damage == "duplicate_sink":
        meta["responseFieldEvidence"]["records"].append(deepcopy(row))
    elif damage == "offset_unit":
        meta["responseFieldEvidence"]["offset_unit"] = "python_characters"
    elif damage == "atlas_hash":
        meta["features"][-1] = "Hash:" + "0" * 64
    else:
        row["confirmed_runtime_defect"] = True
    result = _response_field_evidence([meta], source)
    assert result["status"] == "unavailable" and result["records"] == []
    assert result["reason"], "Missing evidence is not an observed empty/clean scan"


def _local_parser_result_symbols(source, target):
    node = shutil.which("node")
    assert node, "Node is required for this syntax-provenance contract"
    root = Path(__file__).resolve().parents[2]
    target.write_bytes(source.encode("utf-8"))
    result = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(target)],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("case", json.loads((Path(__file__).resolve().parents[2] / "config" /
    "target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))["response_json_return_cases"],
    ids=lambda case: case["id"])
def test_response_json_return_preserves_whole_field_cast_and_utf8_identity(case, tmp_path):
    from tools.engines.generate_atlas import _response_json_return_evidence, _client_local_parser_result_evidence, _normalize_polyglot_symbols
    source = "\ufeff// \U0001f680\r\n" + case["source"]
    symbols = _local_parser_result_symbols(source, tmp_path / "adapter.ts")
    evidence = _response_json_return_evidence(symbols, source)
    assert evidence["status"] == "observed" and len(evidence["records"]) == 1
    row = evidence["records"][0]
    for key in ("field_path", "callable_kind", "return_type_assertion"):
        assert row[key] == case[key]
    assert row["callable_name"] == "load"
    normalized = next(symbol for symbol in _normalize_polyglot_symbols(symbols, source, "typescript") if symbol["name"] == "load")
    assert normalized["response_json_callable_kind"] == case["callable_kind"]
    assert row["spans"]["callable"]["start"] == normalized["start"] and row["spans"]["callable"]["end"] == normalized["end"]
    raw = source.encode("utf-8")
    def text(role):
        span = row["spans"][role]
        return raw[span["start"]:span["end"]].decode("utf-8")
    assert text("json_ref") == "raw" and text("callable_binding") == "load"
    assert text("response_ref") == "response" and text("return_statement").startswith("return ")
    assert ("type_assertion" in row["spans"]) == case["return_type_assertion"]
    assert evidence["promise_resolution"] == evidence["caller_state_binding"] == evidence["schema_authority"] == "not_established"
    assert evidence["runtime_proof"] == "not_performed"
    assert _client_local_parser_result_evidence(symbols, source)["records"] == []
    meta = next(row for row in symbols if row["name"] == "__file_meta__")
    assert not any(feature.startswith("React:ExternalResponse") for feature in meta["features"])


@pytest.mark.parametrize("outer", [
    "export async function Panel() { BODY }",
    "export const Panel = () => { BODY };",
    "export const Panel = () => { BODY return <section />; };"])
def test_local_arrow_import_call_keeps_explicit_owner_and_utf8_span(outer, tmp_path, monkeypatch):
    import hashlib
    import tools.engines.generate_atlas as atlas
    source = "\ufeff// π😀\r\nimport { load as read } from './adapter';\r\n" + outer.replace(
        "BODY", "const handle = async () => { const value = await read(); }; read();")
    raw = _local_parser_result_symbols(source, tmp_path / "Panel.tsx")
    original = atlas._native_utf16_span_validator
    calls = []
    def count_once(content):
        calls.append(content)
        return original(content)
    monkeypatch.setattr(atlas, "_native_utf16_span_validator", count_once)
    symbols = atlas._normalize_polyglot_symbols([row for row in raw if row["name"] != "__file_meta__"],
        source, "typescript")
    assert len(calls) == 1, "Span context must be shared across all symbols in a file"
    panel = next(row for row in symbols if row["name"] == "Panel")
    assert len(panel["import_call_evidence"]["calls"]) == 1, "Do not relabel nested calls as direct calls"
    assert "local_arrow_import_call_evidence" not in panel["import_call_evidence"]
    lane = panel["local_arrow_import_call_evidence"]
    assert lane["status"] == "observed" and len(lane["records"]) == 1
    row = lane["records"][0]
    assert row["lexical_owner"] == "handle" and row["localName"] == "read" and row["importedName"] == "load"
    assert lane["source_hash"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert lane["ast_contract_version"] == atlas.AST_CONTRACT_VERSION and lane["offset_unit"] == "utf8_bytes"
    assert lane["runtime_execution"] == "not_established"
    for role, expected in [("owner_binding", "handle"), ("call", "read()"), ("callee", "read")]:
        span = row["spans"][role]
        assert source.encode("utf-8")[span["start"]:span["end"]].decode("utf-8") == expected


@pytest.mark.parametrize("body", [
    "const handle = (read: any) => { read(); };",
    "let handle = () => { read(); };",
    "const handle = function() { read(); };",
    "const handle = (() => { read(); });",
    "const handle = () => { read(); }, other = 1;",
    "queueMicrotask(() => { read(); });",
    "const handle = () => { const deep = () => { read(); }; };",
    "{ const handle = () => { read(); }; }",
    "const handle = () => { if (true) read(); };",
    "const handle = () => { for (;;) { read(); } };",
    "const handle = () => { try { read(); } catch {} };",
    "const handle = () => { true ? read() : null; };",
    "const handle = () => { true && read(); };",
    "const handle = () => { const alias = read; alias(); };",
    "const handle = () => { read?.(); };",
    "const handle = () => read();",
    "const handle = () => { read(); }; const handle = () => {};"])
def test_local_arrow_import_call_excludes_nonfinite_or_shadowed_owners(body, tmp_path):
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    source = "import { load as read } from './adapter'; export function Panel() { " + body + " }"
    raw = _local_parser_result_symbols(source, tmp_path / "Panel.tsx")
    panel = next(row for row in _normalize_polyglot_symbols(raw, source, "typescript") if row["name"] == "Panel")
    assert panel["local_arrow_import_call_evidence"]["records"] == []


@pytest.mark.parametrize("declaration", [
    "import type { load as read } from './adapter';",
    "import { type load as read } from './adapter';",
    "import read from './adapter';",
    "import * as read from './adapter';",
    "import { load as read } from './adapter'; import { load as read } from './adapter';",
    "import { load as read } from './adapter'; const read = () => {};"])
def test_local_arrow_import_call_rejects_nonruntime_named_imports(declaration, tmp_path):
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    source = declaration + " export function Panel() { const handle = () => { read(); }; }"
    raw = _local_parser_result_symbols(source, tmp_path / "Panel.tsx")
    panel = next(row for row in _normalize_polyglot_symbols(raw, source, "typescript") if row["name"] == "Panel")
    assert panel["local_arrow_import_call_evidence"]["records"] == []


@pytest.fixture(scope="module")
def local_arrow_native_input(tmp_path_factory):
    source = "\ufeff// π😀\r\nimport { load as read } from './adapter'; export function Panel() { const handle = () => { read(); }; }"
    raw = _local_parser_result_symbols(source, tmp_path_factory.mktemp("local-arrow") / "Panel.tsx")
    return source, next(row for row in raw if row["name"] == "Panel")


@pytest.mark.parametrize("damage", [
    "hash", "version", "line", "surrogate", "outside_owner", "duplicate", "optional",
    "omitted", "record_cap", "authority", "parser", "missing", "status_list", "status_dict"])
def test_local_arrow_import_call_corrupt_transport_is_unavailable(local_arrow_native_input, damage):
    from copy import deepcopy
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    source, pristine = local_arrow_native_input
    raw = deepcopy(pristine)
    lane = raw["import_call_evidence"]["local_arrow_import_call_evidence"]
    if damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "version": lane["version"] = "future"
    elif damage == "line": lane["records"][0]["spans"]["call"]["line"] += 1
    elif damage == "surrogate": lane["records"][0]["spans"]["callee"]["start"] = 7
    elif damage == "outside_owner": lane["records"][0]["spans"]["owner"]["start"] = 0
    elif damage == "duplicate": lane["records"] *= 2
    elif damage == "optional": lane["records"][0]["optional"] = True
    elif damage == "omitted": lane["omitted"] = 1
    elif damage == "record_cap": lane["records"] *= 65
    elif damage == "authority": lane["runtime_execution"] = "confirmed"
    elif damage == "parser": raw["parserKind"] = "fallback"
    elif damage == "status_list": lane["status"] = []
    elif damage == "status_dict": lane["status"] = {}
    else: del raw["import_call_evidence"]["local_arrow_import_call_evidence"]
    result = _normalize_polyglot_symbols([raw], source, "typescript")[0]
    if damage == "missing":
        assert "local_arrow_import_call_evidence" not in result, "Legacy metadata must not become observed-empty"
    else:
        assert result["local_arrow_import_call_evidence"]["status"] == "unavailable"
        assert result["local_arrow_import_call_evidence"]["records"] == []


def test_local_arrow_import_call_extra_spans_do_not_expand_offset_work(local_arrow_native_input, monkeypatch):
    from copy import deepcopy
    from tools.engines import generate_atlas as engine
    source, pristine = local_arrow_native_input
    raw = deepcopy(pristine)
    spans = raw["import_call_evidence"]["local_arrow_import_call_evidence"]["records"][0]["spans"]
    spans.update({f"extra_{index}": {"start": 0, "end": 1} for index in range(1000)})
    original = engine._utf16_offsets_to_utf8_byte_offsets
    requested = []
    def bounded(content, offsets):
        requested.append(len(offsets))
        return original(content, offsets)
    monkeypatch.setattr(engine, "_utf16_offsets_to_utf8_byte_offsets", bounded)
    without_import_lane = deepcopy(pristine)
    del without_import_lane["import_call_evidence"]["local_arrow_import_call_evidence"]
    engine._normalize_polyglot_symbols([without_import_lane], source, "typescript")
    result = engine._normalize_polyglot_symbols([raw], source, "typescript")[0]
    assert result["local_arrow_import_call_evidence"]["status"] == "unavailable"
    assert len(requested) == 2 and requested[0] == requested[1], "Malformed spans must add no work beyond the same source's legitimate non-import evidence"


@pytest.fixture(scope="module")
def local_arrow_result_use_input(tmp_path_factory):
    source = ("\ufeff// π😀\r\nimport { load as read } from './adapter';\r\n"
              "export function Panel() { const sink = (v: unknown) => {}; "
              "const handle = async () => { const data = await read(); "
              "if (data.tables) { sink(data.tables); } }; }")
    raw = _local_parser_result_symbols(source, tmp_path_factory.mktemp("result-use") / "Panel.tsx")
    return source, next(row for row in raw if row["name"] == "Panel")


def test_local_arrow_result_use_keeps_separate_syntax_and_shared_offsets(local_arrow_result_use_input, monkeypatch):
    from tools.engines import generate_atlas as atlas
    source, raw = local_arrow_result_use_input
    original = atlas._native_utf16_span_validator
    contexts = []
    def once(content):
        contexts.append(content)
        return original(content)
    monkeypatch.setattr(atlas, "_native_utf16_span_validator", once)
    panel = atlas._normalize_polyglot_symbols([raw], source, "typescript")[0]
    assert len(contexts) == 1
    lane = panel["local_arrow_result_use_evidence"]
    assert lane["status"] == "observed" and len(lane["records"]) == 1
    row = lane["records"][0]
    assert (row["result_name"], row["selected_property"], row["sink_name"]) == ("data", "tables", "sink")
    expected = {"owner_binding": "handle", "result_binding": "data", "initializer": "await read()",
                "condition": "data.tables", "argument": "data.tables", "sink_call": "sink(data.tables)",
                "sink_callee": "sink", "sink_binding": "sink", "call": "read()", "callee": "read"}
    for role, text in expected.items():
        span = row["spans"][role]
        assert source.encode("utf-8")[span["start"]:span["end"]].decode("utf-8") == text
    assert row["spans"]["sink_binding"]["end"] < row["spans"]["owner"]["start"]
    assert len(panel["local_arrow_import_call_evidence"]["records"]) == 1
    assert panel["import_call_evidence"]["calls"] == []
    assert "local_arrow_result_use_evidence" not in panel["import_call_evidence"]
    assert lane["runtime_execution"] == "not_established"


@pytest.mark.parametrize("body", [
    "const data = read(); if(data.tables) { sink(data.tables); }",
    "let data = await read(); if(data.tables) { sink(data.tables); }",
    "const data: any = await read(); if(data.tables) { sink(data.tables); }",
    "const {data} = await read(); if(data.tables) { sink(data.tables); }",
    "const data = (await read()) as any; if(data.tables) { sink(data.tables); }",
    "const data = await read() || {}; if(data.tables) { sink(data.tables); }",
    "const data = await read(); if(data?.tables) { sink(data.tables); }",
    "const data = await read(); if(data['tables']) { sink(data['tables']); }",
    "const data = await read(); if(data.tables.deep) { sink(data.tables.deep); }",
    "const data = await read(); if(data.tables) { sink(data.other); }",
    "const data = await read(); if(data.tables) { sink?.(data.tables); }",
    "const data = await read(); if(data.tables) { obj.sink(data.tables); }",
    "const data = await read(); if(data.tables) { sink(data.tables, 1); }",
    "const data = await read(); if(data.tables) { const data = {}; sink(data.tables); }",
    "const data = await read(); if(data.tables) { sink(data.tables); } else {}",
    "const data = await read(); if(data.tables) { sink(data.tables); extra(); }",
    "const data = await read(); const alias = data; if(alias.tables) { sink(alias.tables); }",
    "const data = await read(); if(data.tables) sink(data.tables);",
    "const data = await read(); for(;;) { sink(data.tables); }",
    "read().then(data => { if(data.tables) { sink(data.tables); } });"])
def test_local_arrow_result_use_excludes_wider_grammar(body, tmp_path):
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    source = ("import { load as read } from './adapter'; export function Panel() { "
              "const sink = (v: unknown) => {}; const handle = async () => { " + body + " }; }")
    raw = _local_parser_result_symbols(source, tmp_path / "Panel.tsx")
    panel = next(row for row in _normalize_polyglot_symbols(raw, source, "typescript") if row["name"] == "Panel")
    assert panel["local_arrow_result_use_evidence"]["records"] == []


@pytest.mark.parametrize("damage", ["hash", "version", "line", "bool", "duplicate", "optional",
                                     "omitted", "cap", "authority", "sink_binding", "argument_order", "extra", "missing"])
def test_local_arrow_result_use_corrupt_lane_preserves_call_lane(local_arrow_result_use_input, damage):
    from copy import deepcopy
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    source, pristine = local_arrow_result_use_input
    raw = deepcopy(pristine)
    lane = raw["import_call_evidence"]["local_arrow_result_use_evidence"]
    if damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "version": lane["version"] = "future"
    elif damage == "line": lane["records"][0]["spans"]["condition"]["line"] += 1
    elif damage == "bool": lane["records"][0]["spans"]["condition"]["start"] = True
    elif damage == "duplicate": lane["records"] *= 2
    elif damage == "optional": lane["records"][0]["optional"] = True
    elif damage == "omitted": lane["omitted"] = 1
    elif damage == "cap": lane["records"] *= 65
    elif damage == "authority": lane["runtime_execution"] = "confirmed"
    elif damage == "sink_binding": lane["records"][0]["spans"]["sink_binding"]["end"] = len(source) * 4
    elif damage == "argument_order": lane["records"][0]["spans"]["argument"] = deepcopy(lane["records"][0]["spans"]["sink_callee"])
    elif damage == "extra": lane["records"][0]["spans"]["authority"] = {}
    else: del raw["import_call_evidence"]["local_arrow_result_use_evidence"]
    panel = _normalize_polyglot_symbols([raw], source, "typescript")[0]
    assert panel["local_arrow_import_call_evidence"]["status"] == "observed"
    if damage == "missing": assert "local_arrow_result_use_evidence" not in panel
    else:
        assert panel["local_arrow_result_use_evidence"]["status"] == "unavailable"
        assert panel["local_arrow_result_use_evidence"]["records"] == []


def test_local_arrow_result_use_cap_is_explicit_not_complete(tmp_path):
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    source = "import {load} from './adapter'; export function Panel() { const sink = (v:unknown) => {}; "
    source += " ".join(f"const h{i} = async () => {{ const data = await load(); if(data.tables) {{ sink(data.tables); }} }};" for i in range(65)) + " }"
    raw = _local_parser_result_symbols(source, tmp_path / "Panel.tsx")
    panel = next(row for row in _normalize_polyglot_symbols(raw, source, "typescript") if row["name"] == "Panel")
    lane = panel["local_arrow_result_use_evidence"]
    assert lane["status"] == "incomplete_scan" and lane["omitted"] == 1 and len(lane["records"]) == 64


def test_local_arrow_import_call_cap_is_explicit_not_complete(tmp_path):
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    source = "import { load } from './adapter'; export function Panel() { const handle = () => { " + "load();" * 65 + " }; }"
    raw = _local_parser_result_symbols(source, tmp_path / "Panel.tsx")
    panel = next(row for row in _normalize_polyglot_symbols(raw, source, "typescript") if row["name"] == "Panel")
    evidence = panel["local_arrow_import_call_evidence"]
    assert evidence["status"] == "incomplete_scan" and evidence["omitted"] == 1
    assert len(evidence["records"]) == 64


@pytest.mark.parametrize("damage", ["shadow_fetch", "import_fetch", "same_binding", "parameter_conflict",
    "nested_function", "non_async", "generator", "let_owner", "function_expression", "multi_owner",
    "wrong_json_receiver", "json_argument", "optional_json", "optional_fetch", "non_await_json",
    "alias_statement", "mutation_statement", "if_return", "try_return", "nested_callback",
    "optional_field", "dynamic_field", "fallback_field", "nested_assertion", "satisfies",
    "non_null", "direct_json_return", "wrong_value", "duplicate_owner", "owner_parameter_conflict"])
def test_response_json_return_excludes_unbound_or_wider_grammar(damage, tmp_path):
    from tools.engines.generate_atlas import _response_json_return_evidence
    body = "const response = await fetch('/x'); const raw = await response.json(); return raw.data;"
    source = "export async function load() { " + body + " }"
    if damage == "shadow_fetch": source = source.replace("load()", "load(fetch: any)")
    elif damage == "import_fetch": source = "import {fetch} from 'other'; " + source
    elif damage == "same_binding": source = source.replace("const raw", "const response").replace("return raw", "return response")
    elif damage == "parameter_conflict": source = source.replace("load()", "load(raw: unknown)")
    elif damage == "owner_parameter_conflict": source = source.replace("load()", "load(response: unknown)")
    elif damage == "nested_function": source = "export function outer() { " + source.replace("export ", "") + " }"
    elif damage == "non_async": source = source.replace("async ", "")
    elif damage == "generator": source = source.replace("function load", "function* load")
    elif damage == "let_owner": source = "export let load = async () => { " + body + " };"
    elif damage == "function_expression": source = "export const load = async function() { " + body + " };"
    elif damage == "multi_owner": source = "export const other = 1, load = async () => { " + body + " };"
    elif damage == "duplicate_owner": source += " function load() {}"
    elif damage == "wrong_json_receiver": source = source.replace("response.json()", "other.json()")
    elif damage == "json_argument": source = source.replace("response.json()", "response.json(1)")
    elif damage == "optional_json": source = source.replace("response.json()", "response?.json()")
    elif damage == "optional_fetch": source = source.replace("fetch('/x')", "fetch?.('/x')")
    elif damage == "non_await_json": source = source.replace("await response", "response")
    elif damage == "alias_statement": source = source.replace("return raw.data;", "const alias = raw; return alias.data;")
    elif damage == "mutation_statement": source = source.replace("return raw.data;", "raw.data = 1; return raw.data;")
    elif damage == "if_return": source = source.replace("return raw.data;", "if(raw) return raw.data;")
    elif damage == "try_return": source = "export async function load() { try { " + body + " } catch {} }"
    elif damage == "nested_callback": source = source.replace("return raw.data;", "return () => raw.data;")
    elif damage == "optional_field": source = source.replace("raw.data", "raw?.data")
    elif damage == "dynamic_field": source = source.replace("raw.data", "raw[key]")
    elif damage == "fallback_field": source = source.replace("raw.data", "raw.data || []")
    elif damage == "nested_assertion": source = source.replace("raw.data", "(raw as Items).data as Items")
    elif damage == "satisfies": source = source.replace("raw.data", "raw.data satisfies Items")
    elif damage == "non_null": source = source.replace("raw.data", "raw.data!")
    elif damage == "direct_json_return": source = source.replace("const raw = await response.json(); return raw.data;", "return response.json();")
    elif damage == "wrong_value": source = source.replace("return raw.data;", "return response;")
    evidence = _response_json_return_evidence(_local_parser_result_symbols(source, tmp_path / "adapter.ts"), source)
    assert evidence["records"] == []


@pytest.fixture(scope="module")
def response_json_native_input(tmp_path_factory):
    source = "\ufeff// \U0001f680\r\nexport const load = async () => {\r\nconst response = await fetch('/x');\r\nconst raw = await response.json();\r\nreturn raw.data as Items;\r\n};"
    return source, _local_parser_result_symbols(source, tmp_path_factory.mktemp("return-native") / "adapter.ts")


@pytest.mark.parametrize("damage", ["meta_missing", "meta_duplicate", "parser", "missing", "version", "status",
    "hash", "offset_unit", "count", "boolean_count", "hash_feature", "extra", "kind", "kind_list", "field",
    "cast", "cast_span", "span_bool", "span_end", "span_surrogate", "line", "order", "containment", "duplicate"])
def test_response_json_return_native_normalization_fails_closed(response_json_native_input, damage):
    from copy import deepcopy
    from tools.engines.generate_atlas import _response_json_return_evidence
    source, original = response_json_native_input
    symbols = deepcopy(original)
    meta = next(row for row in symbols if row["name"] == "__file_meta__")
    evidence = meta["responseJsonReturnEvidence"]
    row = evidence["records"][0]
    if damage == "meta_missing": symbols.remove(meta)
    elif damage == "meta_duplicate": symbols.append(deepcopy(meta))
    elif damage == "parser": meta["parserStatus"] = "degraded"
    elif damage == "missing": meta.pop("responseJsonReturnEvidence")
    elif damage == "version": evidence["version"] = "old"
    elif damage == "status": evidence["status"] = "degraded"
    elif damage == "hash": evidence["source_hash"] = "0" * 64
    elif damage == "offset_unit": evidence["offset_unit"] = "utf8_bytes"
    elif damage == "count": evidence["record_count"] += 1
    elif damage == "boolean_count": evidence["record_count"] = True
    elif damage == "hash_feature": meta["features"].append("Hash:" + "0" * 64)
    elif damage == "extra": row["validated"] = True
    elif damage == "kind": row["callable_kind"] = "callback"
    elif damage == "kind_list": row["callable_kind"] = []
    elif damage == "field": row["field_path"] = [1]
    elif damage == "cast": row["return_type_assertion"] = "true"
    elif damage == "cast_span": row["spans"].pop("type_assertion")
    elif damage == "span_bool": row["spans"]["json_ref"]["start"] = True
    elif damage == "span_end": row["spans"]["json_ref"]["end"] = 100000
    elif damage == "span_surrogate": row["spans"]["json_ref"]["start"] = 5
    elif damage == "line": row["spans"]["json_ref"]["line"] += 1
    elif damage == "order": row["spans"]["fetch"], row["spans"]["json"] = row["spans"]["json"], row["spans"]["fetch"]
    elif damage == "containment": row["spans"]["selected_expression"] = deepcopy(row["spans"]["body"])
    elif damage == "duplicate": evidence["records"].append(deepcopy(row)); evidence["record_count"] += 1
    normalized = _response_json_return_evidence(symbols, source)
    assert normalized["status"] == "unavailable" and normalized["records"] == []
    assert normalized["source_hash"] is None and normalized["schema_authority"] == "not_established"


@pytest.mark.parametrize("case", json.loads((Path(__file__).resolve().parents[2] / "config" /
    "target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))["imported_parser_cases"],
    ids=lambda case: case["id"])
def test_imported_parser_records_exact_input_result_and_named_import_without_authority(case, tmp_path):
    from tools.engines.generate_atlas import _client_imported_parser_evidence, _client_local_parser_result_evidence, _client_local_parser_input_sink_evidence
    source = "\ufeff// \U0001f680\r\n" + case["source"].replace("\n", "\r\n")
    symbols = _local_parser_result_symbols(source, tmp_path / "Panel.tsx")
    evidence = _client_imported_parser_evidence(symbols, source)
    assert evidence["status"] == "observed" and len(evidence["records"]) == 1
    row = evidence["records"][0]
    assert row["selected_value_origin"] == case["selected_value_origin"]
    assert row["import_binding"] == {"source": "./schema", "kind": "named", "imported_name": "Schema", "local_name": "Parser", "type_only": False}
    def captured(role):
        span = row["spans"][role]
        return source.encode("utf-8")[span["start"]:span["end"]].decode("utf-8")
    assert captured("parser_input") == captured("json_binding") == "raw"
    assert captured("parser_owner_binding") == captured("parser_owner_ref") == "Parser"
    assert captured("parser_owner") == "import {Schema as Parser} from './schema';"
    assert captured("argument") == ("raw.image" if case["selected_value_origin"] == "json_input" else "parsed.image")
    assert evidence["parser_effect_on_input"] == evidence["schema_authority"] == "not_established"
    assert evidence["runtime_proof"] == "not_performed"
    assert _client_local_parser_result_evidence(symbols, source)["records"] == []
    assert _client_local_parser_input_sink_evidence(symbols, source)["records"] == []
    meta = next(row for row in symbols if row["name"] == "__file_meta__")
    assert not any(feature.startswith("React:ExternalResponse") for feature in meta["features"])


@pytest.mark.parametrize(("before", "after"), [
    ("{Schema as Parser}", "type {Schema as Parser}"),
    ("{Schema as Parser}", "{type Schema as Parser}"),
    ("{Schema as Parser}", "* as Parser"),
    ("{Schema as Parser}", "Parser"),
    ("async function load(){", "async function load(Parser){"),
    ("async function load(){", "async function load(){ const Parser={parse:x=>x};"),
    ("from './schema';", "from './schema'; import {Other as Parser} from './other';"),
    ("const raw=", "const raw=0; const raw="),
    ("const parsed=", "const parsed=0; const parsed="),
    ("Parser.parse(raw)", "await Parser.parse(raw)"),
    ("Parser.parse(raw)", "Parser.safeParse(raw)"),
    ("Parser.parse(raw)", "Parser.parse(raw, {})"),
    ("Parser.parse(raw)", "Parser?.parse(raw)"),
    ("Parser.parse(raw)", "Parser['parse'](raw)"),
    ("setImage(parsed.image);", "const other=parsed; setImage(other.image);"),
    ("setImage(parsed.image);", "setImage(parsed.image); console.log('later');"),
    ("setImage(parsed.image);", "setImage(parsed.image as string);"),
    ("async function load(){", "const load=async()=>{"),
    ("Parser.parse(raw)", "Parser.parse({image:'request'})"),
])
def test_imported_parser_rejects_non_runtime_shadowed_ambiguous_or_nonfinite_binding(before, after, tmp_path):
    from tools.engines.generate_atlas import _client_imported_parser_evidence
    fixture = json.loads((Path(__file__).resolve().parents[2] / "config/target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))
    source = fixture["imported_parser_cases"][0]["source"].replace(before, after)
    assert source != fixture["imported_parser_cases"][0]["source"]
    symbols = _local_parser_result_symbols(source, tmp_path / "Panel.tsx")
    assert _client_imported_parser_evidence(symbols, source)["records"] == []


@pytest.mark.parametrize("damage", ["count", "version", "hash", "unit", "origin", "origin_list", "type_only", "kind", "binding_keys", "row_keys", "selected_ref", "relations", "span_bool", "duplicate", "source", "imported_name", "local_name", "direct_binding"])
def test_imported_parser_normalizer_rejects_corrupt_contract(tmp_path, damage):
    from tools.engines.generate_atlas import _client_imported_parser_evidence
    fixture = json.loads((Path(__file__).resolve().parents[2] / "config/target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))
    source = fixture["imported_parser_cases"][0]["source"]
    symbols = _local_parser_result_symbols(source, tmp_path / "Panel.tsx")
    meta = next(row for row in symbols if row["name"] == "__file_meta__")["clientImportedParserEvidence"]
    row = meta["records"][0]
    if damage in {"count", "version", "hash", "unit"}:
        key = {"count": "record_count", "hash": "source_hash", "unit": "offset_unit"}.get(damage, damage)
        meta[key] = 5 if damage == "count" else "invalid"
    elif damage == "origin": row["selected_value_origin"] = "validated"
    elif damage == "origin_list": row["selected_value_origin"] = []
    elif damage == "type_only": row["import_binding"]["type_only"] = True
    elif damage == "kind": row["import_binding"]["kind"] = "namespace"
    elif damage == "binding_keys": row["import_binding"]["extra"] = True
    elif damage in {"source", "imported_name", "local_name"}: row["import_binding"][damage] = "other"
    elif damage == "direct_binding": next(row for row in symbols if row["name"] == "__file_meta__")["directImportBindings"] = []
    elif damage == "row_keys": row["validated"] = True
    elif damage == "selected_ref": row["spans"]["input_ref"] = row["spans"].pop("result_ref")
    elif damage == "relations": row["spans"]["parser_input"] = row["spans"]["sink"]
    elif damage == "span_bool": row["spans"]["sink"]["line"] = True
    else:
        meta["records"].append(row); meta["record_count"] = 2
    evidence = _client_imported_parser_evidence(symbols, source)
    assert evidence["status"] == "unavailable" and evidence["records"] == [] and evidence["reason"]


@pytest.mark.parametrize("case", json.loads((Path(__file__).resolve().parents[2] / "config" /
    "target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))["local_parser_result_cases"],
    ids=lambda case: case["id"])
def test_local_parser_result_transport_requires_exact_bindings_and_finite_tail(case, tmp_path):
    from tools.engines.generate_atlas import _client_local_parser_result_evidence, _client_local_parser_input_sink_evidence
    import hashlib
    source = case["source"]
    symbols = _local_parser_result_symbols(source, tmp_path / "Panel.tsx")
    meta = next(row for row in symbols if row["name"] == "__file_meta__")
    assert meta["parserStatus"] == case["parser_status"]
    evidence = _client_local_parser_result_evidence(symbols, source)
    assert evidence["status"] == ("observed" if case["parser_status"] == "observed" else "unavailable")
    assert len(evidence["records"]) == case["expected_records"]
    input_evidence = _client_local_parser_input_sink_evidence(symbols, source)
    assert len(input_evidence["records"]) == (1 if case["id"] == "discarded_result_raw_sink" else 0)
    assert evidence["schema_authority"] == "not_established" and evidence["runtime_proof"] == "not_performed"
    if evidence["status"] == "observed":
        assert evidence["source_hash"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
    encoded = source.encode("utf-8")
    for row in evidence["records"]:
        def captured(role):
            span = row["spans"][role]
            return encoded[span["start"]:span["end"]].decode("utf-8")
        assert captured("json_binding") == captured("parser_input")
        assert captured("result_binding") == captured("result_ref")
        assert captured("parser_owner_binding") == captured("parser_owner_ref")
        assert captured("setter_binding") == captured("setter_ref")
        assert ".parse(" in captured("parser_call")
        assert captured("argument") in captured("sink")
        assert row["field_path"] == ([] if case["id"] == "result_whole" else ["image"])
    features = [feature for feature in meta["features"] if feature.startswith((
        "React:ExternalResponseFieldState:", "React:ExternalResponseDirectState:",
        "React:ExternalResponseStateCast:", "React:ExternalResponseAnyState:"))]
    assert len(features) == case["expected_advisories"], "Old response admission must remain unchanged"
    atlas = {"features": meta["features"], "symbols": []}
    current = analyze_runtime_intelligence_file("fixture", "Panel.tsx", source,
        atlas_file={**atlas, "client_local_parser_result_evidence": evidence,
            "client_local_parser_input_sink_evidence": input_evidence})
    assert current == analyze_runtime_intelligence_file("fixture", "Panel.tsx", source, atlas_file=atlas)


@pytest.mark.parametrize("case", json.loads((Path(__file__).resolve().parents[2] / "config" /
    "target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))["local_parser_input_sink_cases"],
    ids=lambda case: case["id"])
def test_local_parser_input_sink_preserves_rejecting_mutating_and_async_unknowns(case, tmp_path):
    from tools.engines.generate_atlas import _client_local_parser_input_sink_evidence, _client_local_parser_result_evidence
    import hashlib
    source = case["source"]
    symbols = _local_parser_result_symbols(source, tmp_path / "Panel.tsx")
    evidence = _client_local_parser_input_sink_evidence(symbols, source)
    assert evidence["status"] == "observed" and len(evidence["records"]) == case["expected_records"]
    assert evidence["source_hash"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert evidence["selected_value_origin"] == "json_input" and evidence["call_result_use"] == "not_selected_by_this_sink"
    assert evidence["parser_effect_on_input"] == evidence["schema_authority"] == "not_established"
    assert evidence["runtime_proof"] == "not_performed"
    row = evidence["records"][0]
    def captured(role):
        span = row["spans"][role]
        return source.encode("utf-8")[span["start"]:span["end"]].decode("utf-8")
    assert captured("json_binding") == captured("parser_input") == captured("input_ref") == "raw"
    assert captured("result_binding") == "parsed" and captured("argument") == "raw.image"
    assert "result_ref" not in row["spans"] and _client_local_parser_result_evidence(symbols, source)["records"] == []
    meta = next(row for row in symbols if row["name"] == "__file_meta__")
    assert not any(feature.startswith("React:ExternalResponse") for feature in meta["features"])
    atlas = {"features": meta["features"], "symbols": []}
    assert analyze_runtime_intelligence_file("fixture", "Panel.tsx", source, atlas_file=atlas) == analyze_runtime_intelligence_file(
        "fixture", "Panel.tsx", source, atlas_file={**atlas, "client_local_parser_input_sink_evidence": evidence})


@pytest.mark.parametrize(("before", "after", "expected"), [
    ("raw.image", "raw", []),
    ("raw.image", "raw['image']", ["image"]),
    ("raw.image", "raw[0]", ["0"]),
    ("raw.image", "raw.payload.image", ["payload", "image"]),
    ("raw.image", r"r\u0061w.image", ["image"]),
    ("{useState}", "{useState as stateHook}", ["image"]),
    ("Schema.parse(raw)", "await Schema.parse(raw)", None),
    ("Schema.parse(raw)", "Schema.safeParse(raw)", None),
    ("Schema.parse(raw)", "Schema.parse({image:'other'})", None),
    ("raw.image", "raw?.image", None),
    ("raw.image", "raw.image as string", None),
    ("raw.image", "raw.image!", None),
    ("raw.image", "raw.image || ''", None),
    ("const parsed=", "const alias=raw; const parsed=", None),
    ("setImage(raw.image);", "if(raw) setImage(raw.image);", None),
    ("setImage(raw.image);", "queueMicrotask(()=>setImage(raw.image));", None),
    ("const Schema={parse(value:any){if(typeof value.image!=='string')throw new Error('image');return value;}};", "import {Schema} from './schema';", None),
    ("from 'react'", "from 'other-state'", None),
    ("async function load(){", "async function load(Schema:any){", None),
    ("const raw=", "const raw=0; const raw=", None),
    ("const parsed=", "const parsed=0; const parsed=", None),
    ("const Schema=", "const Schema=0; const Schema=", None),
    ("async function load(){", "async function load(setImage:any){", None),
    ("setImage(raw.image);", "setImage(raw.image); return;", None),
], ids=["whole", "string-key", "numeric-key", "nested-key", "escaped-binding", "react-alias",
    "awaited", "safeparse", "unrelated-input", "optional", "cast", "non-null", "fallback", "intervening-alias",
    "branch", "deferred", "imported-owner", "wrong-package", "owner-shadow", "raw-duplicate", "result-duplicate",
    "owner-duplicate", "setter-shadow", "non-final"])
def test_local_parser_input_sink_admits_only_exact_finite_grammar(before, after, expected, tmp_path):
    from tools.engines.generate_atlas import _client_local_parser_input_sink_evidence
    fixture = json.loads((Path(__file__).resolve().parents[2] / "config" /
        "target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))
    original = fixture["local_parser_input_sink_cases"][0]["source"]
    assert before in original
    source = original.replace(before, after)
    if after == "{useState as stateHook}":
        source = source.replace("=useState<", "=stateHook<")
    evidence = _client_local_parser_input_sink_evidence(_local_parser_result_symbols(source, tmp_path / "Panel.tsx"), source)
    assert evidence["status"] == "observed" and len(evidence["records"]) == (0 if expected is None else 1)
    if expected is not None:
        assert evidence["records"][0]["field_path"] == expected
    assert evidence["schema_authority"] == "not_established" and evidence["runtime_proof"] == "not_performed"


@pytest.fixture(scope="module", params=[False, True], ids=["result", "input"])
def local_parser_result_native(tmp_path_factory, request):
    fixture = json.loads((Path(__file__).resolve().parents[2] / "config" /
        "target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))
    source = fixture["local_parser_result_cases"][0]["source"]
    if request.param:
        source = source.replace("setImage(parsed.image)", "setImage(raw.image)")
    return source, _local_parser_result_symbols(source, tmp_path_factory.mktemp("local-result") / "Panel.tsx"), request.param


@pytest.mark.parametrize(("selection", "expected_path", "alias"), [
    ("parsed[0]", ["0"], False),
    ("parsed.payload.image", ["payload", "image"], False),
    ("parsed.image", ["image"], True),
    (r"par\u0073ed.image", ["image"], False),
])
def test_local_parser_result_additional_literal_and_binding_controls(selection, expected_path, alias, tmp_path):
    from tools.engines.generate_atlas import _client_local_parser_result_evidence
    fixture = json.loads((Path(__file__).resolve().parents[2] / "config" /
        "target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))
    source = fixture["local_parser_result_cases"][0]["source"].replace("parsed.image", selection)
    if alias:
        source = source.replace("{useState}", "{useState as stateHook}").replace("=useState<", "=stateHook<")
    evidence = _client_local_parser_result_evidence(_local_parser_result_symbols(source, tmp_path / "Panel.tsx"), source)
    assert evidence["status"] == "observed" and len(evidence["records"]) == 1
    assert evidence["records"][0]["field_path"] == expected_path
    assert evidence["schema_authority"] == "not_established", "Syntax paths do not prove this schema accepts or returns them"


@pytest.mark.parametrize("damage", ["missing", "version", "degraded", "hash", "count", "boolean_offset",
    "bounds", "offset_unit", "atlas_hash", "role_missing", "path", "claim", "binding_span", "order",
    "duplicate", "unknown_header", "meta_duplicate", "body_containment", "line"])
def test_local_parser_result_transport_rejects_unavailable_or_malformed_evidence(damage, local_parser_result_native):
    from copy import deepcopy
    from tools.engines.generate_atlas import _client_local_parser_result_evidence, _client_local_parser_input_sink_evidence
    source, native, input_sink = local_parser_result_native
    normalizer = _client_local_parser_input_sink_evidence if input_sink else _client_local_parser_result_evidence
    metadata_key = "clientLocalParserInputSinkEvidence" if input_sink else "clientLocalParserResultEvidence"
    symbols = deepcopy(native)
    assert normalizer(symbols, source)["status"] == "observed"
    meta = next(row for row in symbols if row["name"] == "__file_meta__")
    evidence = meta[metadata_key]
    row = evidence["records"][0]
    if damage == "missing":
        meta.pop(metadata_key)
    elif damage == "version":
        evidence["version"] = "unverified"
    elif damage == "degraded":
        meta["parserStatus"] = "degraded"
    elif damage == "hash":
        evidence["source_hash"] = "0" * 64
    elif damage == "count":
        evidence["records"] = []
    elif damage == "boolean_offset":
        row["spans"]["sink"]["start"] = True
    elif damage == "bounds":
        row["spans"]["sink"]["end"] = 10000
    elif damage == "offset_unit":
        evidence["offset_unit"] = "utf8_bytes"
    elif damage == "atlas_hash":
        meta["features"] = [feature for feature in meta["features"] if not feature.startswith("Hash:")]
    elif damage == "role_missing":
        row["spans"].pop("parser_input")
    elif damage == "path":
        row["field_path"] = "image"
    elif damage == "claim":
        row["validated"] = True
    elif damage == "binding_span":
        row["spans"]["input_ref" if input_sink else "result_ref"] = row["spans"]["json_binding"]
    elif damage == "order":
        row["spans"]["fetch"], row["spans"]["json"] = row["spans"]["json"], row["spans"]["fetch"]
    elif damage == "duplicate":
        evidence["records"].append(deepcopy(row))
        evidence["record_count"] += 1
    elif damage == "unknown_header":
        evidence["schema_authority"] = "validated"
    elif damage == "meta_duplicate":
        symbols.append(deepcopy(meta))
    elif damage == "body_containment":
        row["spans"]["body"] = row["spans"]["fetch"]
    else:
        row["spans"]["parser_input"]["line"] += 1
    result = normalizer(symbols, source)
    assert result["status"] == "unavailable" and result["records"] == [] and result["reason"]


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r", "\u2029"])
@pytest.mark.parametrize("input_sink", [False, True], ids=["result", "input"])
def test_local_parser_result_transport_preserves_canonical_unicode_coordinates(newline, input_sink, tmp_path):
    from tools.engines.generate_atlas import _client_local_parser_result_evidence, _client_local_parser_input_sink_evidence
    fixture = json.loads((Path(__file__).resolve().parents[2] / "config" /
        "target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))
    source = ("\ufeff// \U0001f680 exact source bytes\n" + fixture["local_parser_result_cases"][0]["source"]).replace("\n", newline)
    if input_sink:
        source = source.replace("setImage(parsed.image)", "setImage(raw.image)")
    symbols = _local_parser_result_symbols(source, tmp_path / "Panel.tsx")
    normalizer = _client_local_parser_input_sink_evidence if input_sink else _client_local_parser_result_evidence
    evidence = normalizer(symbols, source)
    assert evidence["status"] == "observed" and len(evidence["records"]) == 1
    assert evidence["offset_unit"] == "utf8_bytes"
    encoded = source.encode("utf-8")
    for role, span in evidence["records"][0]["spans"].items():
        assert encoded[span["start"]:span["end"]].decode("utf-8")
    span = evidence["records"][0]["spans"]["parser_input"]
    assert encoded[span["start"]:span["end"]] == b"raw"


def test_local_parser_result_transport_survives_sqlite_primary_reload(tmp_path, monkeypatch):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    from tools.engines.generate_atlas import file_contract_is_current
    import tools.core.json_io as json_io
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads((root / "config/target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))
    source = fixture["local_parser_result_cases"][0]["source"]
    legacy_source = next(case["source"] for case in fixture["field_state_cases"] if case["id"] == "branch_literal_fallback")
    target = tmp_path / "target"
    target.mkdir()
    (target / "Panel.tsx").write_bytes(source.encode("utf-8"))
    (target / "Raw.tsx").write_bytes(legacy_source.encode("utf-8"))
    input_source = fixture["local_parser_input_sink_cases"][0]["source"]
    (target / "Input.tsx").write_bytes(input_source.encode("utf-8"))
    env = dict(os.environ)
    for key in ("CODEMAPS_TARGET_PREFLIGHT_RECEIPT", "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256",
            "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE", "CODEMAPS_TARGET_PROJECTS"):
        env.pop(key, None)
    env["CODEMAPS_TARGET_ROOT"] = str(target)
    result = subprocess.run([sys.executable, "-B", "-m", "tools.engines.generate_atlas"],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stderr or result.stdout
    raw = root / "output/external_targets" / external_target_output_slug(str(target)) / ".raw"
    assert (raw / "codemaps.db").is_file()
    native_read = json_io.load_json_file
    def reject_atlas_shadow(path, *args, **kwargs):
        assert Path(path) != raw / "atlas.json", "Real SQLite primary is required"
        return native_read(path, *args, **kwargs)
    monkeypatch.setattr(json_io, "load_json_file", reject_atlas_shadow)
    files = load_atlas_data(raw)["MAIN"]["files"]
    data = files["Panel.tsx"]
    assert file_contract_is_current(data)
    assert not file_contract_is_current({**data, "ast_contract_version": "v18.32-response-field-evidence-transport"})
    assert not file_contract_is_current({**data, "ast_contract_version": "v18.33-client-local-parser-result-evidence"})
    evidence = data["client_local_parser_result_evidence"]
    assert evidence["status"] == "observed" and len(evidence["records"]) == 1
    assert evidence["source_hash"] == data["hash"] and evidence["ast_contract_version"] == data["ast_contract_version"]
    assert evidence["schema_authority"] == "not_established" and evidence["runtime_proof"] == "not_performed"
    assert data["response_field_evidence"]["records"] == []
    input_data = files["Input.tsx"]
    input_evidence = input_data["client_local_parser_input_sink_evidence"]
    assert input_evidence["status"] == "observed" and len(input_evidence["records"]) == 1
    assert input_evidence["source_hash"] == input_data["hash"]
    assert input_evidence["selected_value_origin"] == "json_input"
    assert input_evidence["call_result_use"] == "not_selected_by_this_sink"
    assert input_evidence["parser_effect_on_input"] == "not_established"
    assert input_data["client_local_parser_result_evidence"]["records"] == []
    old = analyze_runtime_intelligence_file("MAIN", "Raw.tsx", legacy_source, atlas_file=files["Raw.tsx"])
    rows = [row for row in old["findings"] if row["risk"] == "external_response_field_to_typed_react_state"]
    assert len(rows) == 1 and rows[0]["confidence"] == "needs_runtime_proof"


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
        ("", "React:ExternalResponseFieldState:4:2"),
        ("import { useState } from 'react';", "React:ExternalResponseFieldState:40:20"),
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
