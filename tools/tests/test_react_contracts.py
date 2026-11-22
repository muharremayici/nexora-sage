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


def _effect_safeparse_source():
    # Intentionally not a rejecting schema. Spelling/source flow is not validation proof.
    return """import {useEffect} from 'react';
const Schema = {safeParse(value: unknown) {return {success: true, data: value};}};
export const Panel = ({onValueChange}: {onValueChange: (value: unknown) => void}) => {
 const formValues = useWatch({control});
 useEffect(() => {
  const result = Schema.safeParse(formValues);
  if (result.success) { onValueChange({type: 'date', ...result.data}); }
 }, [formValues]);
 return <form />;
};
"""


def _early_return_source():
    return """import {schemaFactory} from './never_execute';
const {email} = useContext();
const complete = async () => {
 try {
  const {success: isValid} = schemaFactory().safeParse(email);
  if (!isValid) { showError('bad'); expand(true); return; }
  await submit(email);
 } catch (error) { report(error); }
};
"""


def _early_return_cases():
    source = _early_return_source()
    cases = {"positive": (source, 1), "empty": (_effect_safeparse_source(), 0),
        "unicode": ("\ufeff// 😀\r\n" + source.replace("\n", "\r\n"), 1)}
    replacements = {
        "factory_alias": ("{schemaFactory}", "{schemaFactory as otherFactory}"),
        "renamed_factory": ("schemaFactory", "ordinaryFunction"),
        "shorthand": ("success: isValid", "success"),
        "plain_input": ("const {email} = useContext();", "const email = readInput();"),
        "array_input": ("const {email} = useContext();", "const [email] = useState('');"),
        "no_prefix": ("showError('bad'); expand(true); ", ""),
        "no_semicolon": ("return;", "return\n"),
        "sync": ("async ()", "()"),
        "type_only": ("import {schemaFactory}", "import type {schemaFactory}"),
        "default_import": ("import {schemaFactory}", "import schemaFactory"),
        "local_factory": ("import {schemaFactory} from './never_execute';", "const schemaFactory = () => ({safeParse: (x) => ({success:true, data:x})});"),
        "factory_arguments": ("schemaFactory()", "schemaFactory(options)"),
        "factory_optional": ("schemaFactory()", "schemaFactory?.()"),
        "factory_generic": ("schemaFactory()", "schemaFactory<string>()"),
        "factory_cast": ("schemaFactory()", "(schemaFactory() as any)"),
        "parser_optional": (".safeParse(email)", ".safeParse?.(email)"),
        "parser_property_optional": (".safeParse(email)", "?.safeParse(email)"),
        "parser_computed": (".safeParse(email)", "['safeParse'](email)"),
        "parser_generic": (".safeParse(email)", ".safeParse<string>(email)"),
        "awaited": ("= schemaFactory()", "= await schemaFactory()"),
        "extra_binding": ("success: isValid", "success: isValid, data"),
        "binding_default": ("success: isValid", "success: isValid = true"),
        "computed_binding": ("success: isValid", "['success']: isValid"),
        "wrong_property": ("success: isValid", "valid: isValid"),
        "mutable_result": ("const {success", "let {success"),
        "mutable_input": ("const {email}", "let {email}"),
        "default_input": ("const {email}", "const {email = ''}"),
        "input_cast": (".safeParse(email)", ".safeParse(email as any)"),
        "input_alias": (".safeParse(email)", ".safeParse(otherEmail)"),
        "non_adjacent": ("  if (!isValid)", "  observe();\n  if (!isValid)"),
        "positive_guard": ("!isValid", "isValid"),
        "wrong_guard": ("!isValid", "!other"),
        "compared_guard": ("!isValid", "isValid === false"),
        "else": ("return; }", "return; } else { submit(); }"),
        "return_value": ("return;", "return false;"),
        "conditional_return": ("return;", "if (other) {return;}"),
        "throw": ("return;", "throw new Error('bad');"),
        "unreachable_tail": ("return;", "return; observe();"),
        "branch_assignment": ("expand(true);", "expanded = true;"),
        "callback_parameter": ("async ()", "async (event)"),
        "callback_mutable": ("const complete", "let complete"),
        "input_write": ("  await submit", "  email = 'other';\n  await submit"),
        "success_write": ("  await submit", "  isValid = true;\n  await submit"),
        "factory_write": ("  await submit", "  schemaFactory = other;\n  await submit"),
        "callback_write": ("  await submit", "  complete = other;\n  await submit"),
        "input_collision": ("const complete", "function other(email: unknown) {}\nconst complete"),
    }
    positive = {"factory_alias", "renamed_factory", "shorthand", "plain_input", "array_input", "no_prefix", "no_semicolon", "sync"}
    for name,(before,after) in replacements.items():
        assert before in source, name
        text = source.replace(before,after)
        if name == "factory_alias": text = text.replace("= schemaFactory()", "= otherFactory()")
        if name == "shorthand": text = text.replace("!isValid", "!success")
        if name == "sync": text = text.replace("await submit", "submit")
        cases[name] = (text, int(name in positive))
    cases["direct_body"] = (source.replace(" try {\n", "").replace(" } catch (error) { report(error); }\n", ""), 1)
    cases["nested_try"] = (source.replace(" try {", " try { try {").replace(" } catch (error)", " } finally {} } catch (error)"), 0)
    cases["diagnostics"] = (source + "const broken = ;", 0)
    cases["byte_budget"] = (source + "/*" + "x" * 1048576 + "*/", 0)
    cases["node_budget"] = (source + "\n" + "\n".join(f"const v{i} = {i};" for i in range(2600)), 0)
    imported, component = source.split("\n", 1)
    parts = []
    for i in range(65):
        text = component
        for name in ("email", "complete", "isValid"): text = text.replace(name,name + str(i))
        parts.append(text)
    cases["record_budget"] = (imported + "\n" + "\n".join(parts), 64)
    return cases


def _effect_merge_source():
    return _effect_safeparse_source().replace("({onValueChange}:", "({value = {type: 'checkbox', direction: 'vertical'}, onValueChange}:").replace(
        "{type: 'date', ...result.data}", "{...value, ...result.data}")


def _effect_merge_cases():
    source = _effect_merge_source()
    cases = {"positive": (source, 1), "unicode": ("\ufeff// 😀\r\n" + source.replace("\n", "\r\n"), 1),
        "empty": (_effect_safeparse_source(), 0)}
    replacements = {
        "no_default": ("value = {type: 'checkbox', direction: 'vertical'}", "value"),
        "renamed": ("value = {type: 'checkbox', direction: 'vertical'}", "value: previous = {type: 'checkbox'}"),
        "nested_shadow": (" const formValues", " const other = (value: unknown) => { value = 2; return value; };\n const formValues"),
        "type_spelling": (" const formValues", " type Predicate = (value: unknown) => boolean;\n const formValues"),
        "alias": ("...value,", "...alias,"),
        "cast": ("...value,", "...(value as any),"),
        "property": ("...value,", "...value.current,"),
        "call": ("...value,", "...value(),"),
        "computed_prior": ("value = {", "[key]: value = {"),
        "default_call": ("{type: 'checkbox', direction: 'vertical'}", "makeDefault()"),
        "default_spread": ("{type: 'checkbox', direction: 'vertical'}", "{...other}"),
        "default_duplicate": ("direction: 'vertical'", "type: 'duplicate'"),
        "default_getter": ("direction: 'vertical'", "get direction() {return 'x';}"),
        "default_computed": ("direction: 'vertical'", "[direction]: 'vertical'"),
        "extra_spread": ("...value,", "...value, ...other,"),
        "extra_property": ("...value,", "...value, type: 'other',"),
        "reordered": ("...value, ...result.data", "...result.data, ...value"),
        "same_callback": ("...value,", "...onValueChange,"),
        "same_scope_collision": (" const formValues", " if (opaque) {var value;}\n const formValues"),
        "block_shadow": (" const formValues", " if (opaque) {let value;}\n const formValues"),
        "direct_write": (" const formValues", " value = other;\n const formValues"),
        "property_write": (" const formValues", " function later() {value.x = 2;}\n const formValues"),
        "destructure_write": (" const formValues", " ({slot: value} = other);\n const formValues"),
        "loop_write": (" const formValues", " for (value of other) {}\n const formValues"),
        "delete": (" const formValues", " delete value.x;\n const formValues"),
        "optional": ("onValueChange({", "onValueChange?.({"),
        "wrong_data": ("result.data", "other.data"),
        "negative_guard": ("if (result.success)", "if (!result.success)"),
        "deferred": ("onValueChange({", "queue(() => onValueChange({"),
    }
    for name, (before, after) in replacements.items():
        assert before in source, name
        text = source.replace(before, after)
        if name == "renamed": text = text.replace("...value,", "...previous,")
        if name == "deferred": text = text.replace("...result.data});", "...result.data}));")
        cases[name] = (text, int(name in {"no_default", "renamed", "nested_shadow", "type_spelling"}))
    cases["diagnostics"] = (source + "const broken = ;", 0)
    cases["byte_budget"] = (source + "/*" + "x" * 1048576 + "*/", 0)
    cases["node_budget"] = (source + "\n" + "\n".join(f"const v{i} = {i};" for i in range(2600)), 0)
    prefix, component = source.split("export const Panel", 1)
    parts = []
    for i in range(65):
        text = "export const Panel" + component
        for token in ("Panel", "onValueChange", "formValues", "result"):
            text = text.replace(token, token + str(i))
        parts.append(text)
    cases["record_budget"] = (prefix + "\n".join(parts), 64)
    return cases


def _effect_safeparse_cases():
    source = _effect_safeparse_source()
    cases = {"positive": (source, 1), "empty": ("export const unused = 1;", 0)}
    replacements = {
        "hook_alias": ("{useEffect}", "{useEffect as effect}"),
        "type_only": ("import {useEffect}", "import type {useEffect}"),
        "impostor": ("import {useEffect} from 'react';", "declare function useEffect(...args: unknown[]): void;"),
        "wrong_package": ("from 'react'", "from 'other'"),
        "namespace": ("import {useEffect}", "import * as React"),
        "wrong_guard": ("result.success", "other.success"),
        "negated": ("result.success", "!result.success"),
        "comparison": ("result.success", "result.success === true"),
        "else": ("...result.data}); }", "...result.data}); } else { report(); }"),
        "raw_spread": ("...result.data", "...formValues"),
        "wrong_data": ("...result.data", "...other.data"),
        "data_cast": ("...result.data", "...(result.data as any)"),
        "spread_not_last": ("type: 'date', ...result.data", "...result.data, type: 'date'"),
        "effect_deferred": ("onValueChange({type: 'date', ...result.data});", "queueMicrotask(() => onValueChange({type: 'date', ...result.data}));"),
        "extra_branch_statement": ("...result.data});", "...result.data}); report();"),
        "awaited": ("Schema.safeParse(formValues)", "await Schema.safeParse(formValues)"),
        "async_effect": ("useEffect(() =>", "useEffect(async () =>"),
        "optional_call": ("Schema.safeParse(formValues)", "Schema.safeParse?.(formValues)"),
        "optional_callback": ("onValueChange({", "onValueChange?.({"),
        "optional_guard": ("result.success", "result?.success"),
        "element_data": ("result.data", "result['data']"),
        "mutable_result": ("const result =", "let result ="),
        "mutable_input": ("const formValues =", "let formValues ="),
        "no_dependencies": ("[formValues]", "[]"),
        "wrong_dependency": ("[formValues]", "[other]"),
        "default_callback": ("({onValueChange}:", "({onValueChange = fallback}:"),
        "property_callback": ("onValueChange({type", "props.onValueChange({type"),
        "duplicate_result": ("const result =", "const result = null; const result ="),
        "shadow_result": ("const result =", "if (opaque) {var result;} const result ="),
        "computed_property": ("type: 'date'", "[key]: 'date'"),
        "getter_property": ("type: 'date'", "get type() { return 'date'; }"),
        "shorthand_property": ("type: 'date'", "type"),
        "extra_spread": ("type: 'date'", "...opaque"),
        "duplicate_property": ("type: 'date'", "type: 'date', type: 'other'"),
        "effect_trailing": (" }, [formValues]);", " report(); }, [formValues]);"),
        "imported_owner": ("const Schema = {safeParse(value: unknown) {return {success: true, data: value};}};", "import {Schema} from './never_execute';"),
    }
    for name, (before, after) in replacements.items():
        assert before in source, name
        text = source.replace(before, after)
        if name == "hook_alias": text = text.replace(" useEffect(", " effect(")
        if name == "namespace": text = text.replace(" useEffect(", " React.useEffect(")
        cases[name] = (text, int(name == "hook_alias"))
    for name, statement in {
        "input_write": "formValues.x = 2;", "result_write": "result.data = other;",
        "owner_write": "Schema.safeParse = other;", "callback_write": "onValueChange = other;",
        "hook_write": "useEffect = other;", "delete": "delete Schema.safeParse;",
        "update": "result.success++;", "destructure": "({slot: onValueChange} = other);",
        "loop_write": "for (onValueChange of other) {}",
    }.items():
        # result is effect-local: a same-spelled unresolved reference outside the
        # effect is not a write to that binding. Keep this exclusion honest.
        outside = name in {"result_write", "update"}
        cases[name + ("_outside_binding" if outside else "")] = (
            source.replace(" const formValues =", " function later() {" + statement + "}\n const formValues ="), int(outside))
        if outside:
            cases[name] = (source.replace("  if (result.success)", "  " + statement + "\n  if (result.success)"), 0)
    cases["unicode"] = ("\ufeff// 😀 exact source\r\n" + source.replace("\n", "\r\n"), 1)
    cases["diagnostics"] = (source + "const broken = ;", 0)
    cases["byte_budget"] = (source + "/*" + "x" * 1048576 + "*/", 0)
    cases["node_budget"] = (source + "\n" + "\n".join(f"const v{i} = {i};" for i in range(2600)), 0)
    prefix, component = source.split("export const Panel", 1)
    parts = []
    for i in range(65):
        text = "export const Panel" + component
        for token in ("Panel", "onValueChange", "formValues", "result"):
            text = text.replace(token, token + str(i))
        parts.append(text)
    cases["record_budget"] = (prefix + "\n".join(parts), 64)
    return cases


@pytest.fixture(scope="module")
def effect_safeparse_native(tmp_path_factory):
    if not shutil.which("node"): pytest.skip("Node required for native source controls")
    root = Path(__file__).resolve().parents[2]
    directory = tmp_path_factory.mktemp("effect-safeparse-native")
    cases = _effect_safeparse_cases()
    cases.update({"merge_" + name: value for name, value in _effect_merge_cases().items()})
    cases.update({"early_" + name: value for name, value in _early_return_cases().items()})
    paths = []
    for name, (source, _) in cases.items():
        path = directory / (name + ".tsx")
        path.write_bytes(source.encode("utf-8"))
        paths.append(str(path))
    (directory / "never_execute.ts").write_text("throw new Error('MUST_NOT_EXECUTE');", encoding="utf-8")
    result = subprocess.run(["node", str(root / "tools/engines/ast_sequencer.cjs"), "--batch-json", *paths],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    return {name: (source, payload[str(directory / (name + '.tsx'))], count) for name, (source, count) in cases.items()}


@pytest.mark.parametrize("name", list(_early_return_cases()))
def test_early_return_native_bounded_guard_source(name, effect_safeparse_native):
    from tools.engines import generate_atlas as atlas
    source, native, expected = effect_safeparse_native["early_" + name]
    meta = next(row for row in native if row["name"] == "__file_meta__")
    assert meta["safeParseEarlyReturnEvidence"]["record_count"] == expected, (name, meta["safeParseEarlyReturnEvidence"])
    evidence = atlas._safeparse_early_return_evidence(native, source)
    assert evidence["record_count"] == expected, (name, evidence)
    assert evidence["schema_authority"] == evidence["type_compatibility"] == evidence["factory_semantics"] == evidence["continuation_safety"] == "not_established"
    assert evidence["mutation_proposed"] is False and evidence["runtime_proof"] == "not_performed"
    if name in {"diagnostics", "byte_budget", "node_budget"}: assert evidence["status"] == "unavailable"
    elif name == "record_budget": assert evidence["status"] == "partial" and evidence["omitted"] == 1
    else: assert evidence["status"] == "observed"
    if expected:
        spans = evidence["records"][0]["spans"]
        encoded = source.encode("utf-8")
        assert encoded[spans["success_property"]["start"]:spans["success_property"]["end"]] == b"success"
        assert encoded[spans["return"]["start"]:spans["return"]["end"]].startswith(b"return")
        assert set(spans) == atlas.EARLY_RETURN_ROLES


@pytest.mark.parametrize("damage", ["missing", "version", "hash", "parser", "duplicate_meta", "count", "bool_count", "role", "extra_role", "offset", "line", "relation", "claim", "duplicate", "partial", "factory_token", "guard_token", "return_token"])
def test_early_return_transport_refuses_corrupt_source(damage, effect_safeparse_native):
    from copy import deepcopy
    from tools.engines.generate_atlas import _safeparse_early_return_evidence
    source, raw, _ = effect_safeparse_native["early_positive"]
    native = deepcopy(raw)
    meta = next(row for row in native if row["name"] == "__file_meta__")
    lane = meta["safeParseEarlyReturnEvidence"]
    if damage == "missing": meta.pop("safeParseEarlyReturnEvidence")
    elif damage == "version": lane["version"] = "old"
    elif damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "parser": meta["parserStatus"] = "degraded"
    elif damage == "duplicate_meta": native.append(deepcopy(meta))
    elif damage == "count": lane["record_count"] = 2
    elif damage == "bool_count": lane["record_count"] = True
    elif damage == "role": lane["records"][0]["spans"].pop("guard_ref")
    elif damage == "extra_role": lane["records"][0]["spans"]["validated"] = lane["records"][0]["spans"]["return"]
    elif damage == "offset": lane["records"][0]["spans"]["return"]["start"] = True
    elif damage == "line": lane["records"][0]["spans"]["return"]["line"] += 1
    elif damage == "relation": lane["records"][0]["spans"]["guard_ref"] = lane["records"][0]["spans"]["input_binding"]
    elif damage == "claim": lane["schema_authority"] = "validated"
    elif damage == "duplicate": lane["records"].append(deepcopy(lane["records"][0])); lane["record_count"] = 2
    elif damage == "partial": lane.update(status="partial", omitted=1, reason="record_budget_exceeded")
    elif damage == "factory_token": lane["records"][0]["spans"]["factory_ref"]["end"] -= 1
    elif damage == "guard_token": lane["records"][0]["spans"]["condition"]["start"] += 1
    elif damage == "return_token": lane["records"][0]["spans"]["return"]["start"] += 1
    evidence = _safeparse_early_return_evidence(native, source)
    assert evidence["status"] == "unavailable" and evidence["records"] == [] and evidence["reason"]


@pytest.mark.parametrize("name", ["positive", "empty", "record_budget", "byte_budget"])
def test_early_return_schema_limits_and_unknowns(name, effect_safeparse_native, tmp_path):
    from tools.core.artifact_validator import validate_against_schema
    from tools.engines.generate_atlas import _safeparse_early_return_evidence
    source, native, _ = effect_safeparse_native["early_" + name]
    evidence = _safeparse_early_return_evidence(native, source)
    schema = json.loads((Path(__file__).resolve().parents[2] / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    path = tmp_path / "early-schema.json"
    path.write_text(json.dumps({"$ref": "#/$defs/safeparse_early_return_evidence", "$defs": schema["$defs"]}), encoding="utf-8")
    assert validate_against_schema(path, "early", evidence) == []
    for key,value in (("factory_semantics", "validated"), ("continuation_safety", "safe"), ("mutation_proposed", 0), ("unproved_claim", True)):
        assert validate_against_schema(path, "early", {**evidence, key:value})


def test_early_return_uses_shared_coordinates_and_no_empty_index(effect_safeparse_native, monkeypatch):
    from tools.engines import generate_atlas as atlas
    source, native, _ = effect_safeparse_native["early_unicode"]
    context = {}
    atlas._normalize_polyglot_symbols(native, source, "typescript", file_evidence_context=context)
    def forbid(*args, **kwargs): raise AssertionError("No second coordinate scan")
    monkeypatch.setattr(atlas, "_utf16_offsets_to_utf8_byte_offsets", forbid)
    monkeypatch.setattr(atlas, "_native_utf16_span_validator", forbid)
    assert atlas._safeparse_early_return_evidence(native, source, context)["record_count"] == 1
    source, native, _ = effect_safeparse_native["early_empty"]
    assert atlas._safeparse_early_return_evidence(native, source)["status"] == "observed"


@pytest.mark.parametrize("name", list(_effect_merge_cases()))
def test_effect_merge_native_source_order_and_uncertainty(name, effect_safeparse_native):
    from tools.engines import generate_atlas as atlas
    from tools.core.atlas_ast_contract import EFFECT_SAFE_PARSE_MERGE_ROLES, effect_safe_parse_merge_stored_problem
    from tools.core.source_coordinates import _utf8_span_validator
    source, native, expected = effect_safeparse_native["merge_" + name]
    evidence = atlas._effect_safeparse_merge_evidence(native, source)
    assert evidence["record_count"] == expected, (name, evidence)
    assert atlas._effect_safeparse_callback_evidence(native, source)["record_count"] == int(name == "empty")
    assert evidence["prior_value_semantics"] == evidence["merge_result_semantics"] == "not_established"
    if expected:
        spans = evidence["records"][0]["spans"]
        assert set(spans) == EFFECT_SAFE_PARSE_MERGE_ROLES
        assert spans["prior_spread"]["end"] < spans["spread"]["start"]
        valid = _utf8_span_validator(source, [span for row in evidence["records"] for span in row["spans"].values()])
        assert effect_safe_parse_merge_stored_problem(evidence, evidence["source_hash"], valid) == ""
    if name in {"diagnostics", "byte_budget", "node_budget"}: assert evidence["status"] == "unavailable"
    elif name == "record_budget": assert evidence["status"] == "partial" and evidence["omitted"] == 1
    else: assert evidence["status"] == "observed"


@pytest.mark.parametrize("name", list(_effect_safeparse_cases()))
def test_effect_safeparse_native_finite_grammar(name, effect_safeparse_native):
    import hashlib
    from tools.engines.generate_atlas import _effect_safeparse_callback_evidence, _client_safeparse_success_evidence
    source, native, expected = effect_safeparse_native[name]
    meta = next(row for row in native if row["name"] == "__file_meta__")
    lane = meta["effectSafeParseCallbackEvidence"]
    assert lane["record_count"] == len(lane["records"]) == expected, (name, lane)
    assert lane["source_hash"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
    evidence = _effect_safeparse_callback_evidence(native, source)
    assert evidence["record_count"] == expected, (name, evidence)
    assert _client_safeparse_success_evidence(native, source)["record_count"] == 0
    if name in {"diagnostics", "byte_budget", "node_budget"}:
        assert evidence["status"] == "unavailable" and evidence["reason"]
    elif name == "record_budget":
        assert evidence["status"] == "partial" and evidence["omitted"] == 1
    else:
        assert evidence["status"] == "observed" and evidence["omitted"] == 0
    assert evidence["schema_authority"] == evidence["type_compatibility"] == "not_established"
    assert evidence["runtime_proof"] == "not_performed" and evidence["mutation_proposed"] is False
    if expected:
        data = source.encode("utf-8")
        spans = evidence["records"][0]["spans"]
        for span in spans.values(): assert data[span["start"]:span["end"]].decode("utf-8")
        for role, token in (("success_property", b"success"), ("data_property", b"data"), ("callback_ref", b"onValueChange" if name != "record_budget" else b"onValueChange0")):
            assert data[spans[role]["start"]:spans[role]["end"]] == token


@pytest.mark.parametrize("merge", [False, True])
@pytest.mark.parametrize("damage", ["missing", "version", "hash", "parser", "duplicate_meta", "count", "bool_count", "role", "extra_role", "offset", "line", "relation", "claim", "duplicate", "partial"])
def test_effect_safeparse_transport_refuses_corruption(damage, effect_safeparse_native, merge):
    from copy import deepcopy
    from tools.engines.generate_atlas import _effect_safeparse_callback_evidence, _effect_safeparse_merge_evidence
    source, raw, _ = effect_safeparse_native["merge_positive" if merge else "positive"]
    native = deepcopy(raw)
    meta = next(row for row in native if row["name"] == "__file_meta__")
    key = "effectSafeParseMergeEvidence" if merge else "effectSafeParseCallbackEvidence"
    lane = meta[key]
    if damage == "missing": meta.pop(key)
    elif damage == "version": lane["version"] = "old"
    elif damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "parser": meta["parserStatus"] = "degraded"
    elif damage == "duplicate_meta": native.append(deepcopy(meta))
    elif damage == "count": lane["record_count"] = 2
    elif damage == "bool_count": lane["record_count"] = True
    elif damage == "role": lane["records"][0]["spans"].pop("callback_ref")
    elif damage == "extra_role": lane["records"][0]["spans"]["validated"] = lane["records"][0]["spans"]["sink"]
    elif damage == "offset": lane["records"][0]["spans"]["sink"]["start"] = True
    elif damage == "line": lane["records"][0]["spans"]["sink"]["line"] += 1
    elif damage == "relation": lane["records"][0]["spans"]["callback_ref"] = lane["records"][0]["spans"]["input_binding"]
    elif damage == "claim": lane["schema_authority"] = "validated"
    elif damage == "duplicate": lane["records"].append(deepcopy(lane["records"][0])); lane["record_count"] = 2
    elif damage == "partial": lane.update(status="partial", omitted=1, reason="record_budget_exceeded")
    evidence = (_effect_safeparse_merge_evidence if merge else _effect_safeparse_callback_evidence)(native, source)
    assert evidence["status"] == "unavailable" and evidence["records"] == [] and evidence["reason"]


def test_effect_safeparse_uses_shared_coordinate_batch(effect_safeparse_native, monkeypatch):
    from tools.engines import generate_atlas as atlas
    source, native, _ = effect_safeparse_native["unicode"]
    context = {}
    atlas._normalize_polyglot_symbols(native, source, "typescript", file_evidence_context=context)
    def forbid(*args, **kwargs): raise AssertionError("No second file coordinate scan")
    monkeypatch.setattr(atlas, "_utf16_offsets_to_utf8_byte_offsets", forbid)
    monkeypatch.setattr(atlas, "_native_utf16_span_validator", forbid)
    assert atlas._effect_safeparse_callback_evidence(native, source, context)["record_count"] == 1
    source, native, _ = effect_safeparse_native["merge_unicode"]
    # The normalizer builds the one-file map before the guard is installed.
    monkeypatch.undo()
    context = {}
    atlas._normalize_polyglot_symbols(native, source, "typescript", file_evidence_context=context)
    monkeypatch.setattr(atlas, "_utf16_offsets_to_utf8_byte_offsets", forbid)
    monkeypatch.setattr(atlas, "_native_utf16_span_validator", forbid)
    assert atlas._effect_safeparse_merge_evidence(native, source, context)["record_count"] == 1
    source, native, _ = effect_safeparse_native["empty"]
    assert atlas._effect_safeparse_callback_evidence(native, source)["status"] == "observed"


@pytest.mark.parametrize("merge", [False, True])
@pytest.mark.parametrize("name", ["positive", "empty", "record_budget", "byte_budget"])
def test_effect_safeparse_schema_coverage_and_no_authority(name, effect_safeparse_native, tmp_path, merge):
    from tools.core.artifact_validator import validate_against_schema
    from tools.engines.generate_atlas import _effect_safeparse_callback_evidence, _effect_safeparse_merge_evidence
    source, native, _ = effect_safeparse_native[("merge_" if merge else "") + name]
    evidence = (_effect_safeparse_merge_evidence if merge else _effect_safeparse_callback_evidence)(native, source)
    schema = json.loads((Path(__file__).resolve().parents[2] / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    path = tmp_path / "effect-schema.json"
    key = "effect_safeparse_merge_evidence" if merge else "effect_safeparse_callback_evidence"
    path.write_text(json.dumps({"$ref": "#/$defs/" + key, "$defs": schema["$defs"]}), encoding="utf-8")
    assert validate_against_schema(path, "effect", evidence) == []
    for key, value in (("mutation_proposed", 0), ("schema_authority", "validated"), ("unproved_claim", True)):
        assert validate_against_schema(path, "effect", {**evidence, key: value})
    if merge:
        for key in ("prior_value_semantics", "merge_result_semantics"):
            assert validate_against_schema(path, "effect", {**evidence, key: "known"})


def _safeparse_source():
    # Deliberate non-validator positive: source correspondence cannot borrow Zod semantics.
    return """import {useState} from 'react';
const Schema = {safeParse(value: unknown) { return {success: true, data: value}; }};
export function Panel() {
 const [rows, setRows] = useState<string[]>([]);
 async function load() {
  const response = await fetch('/rows');
  const raw = await response.json();
  const result = Schema.safeParse(raw);
  if (result.success) { setRows(result.data); }
 }
 return <button onClick={load} />;
}
"""


def _safeparse_cases():
    source = _safeparse_source()
    cases = {"positive": (source, 1), "empty": ("export const unused = 1;", 0)}
    replacements = {
        "alias": ("useState}", "useState as stateHook}"),
        "imported_owner": ("const Schema = {safeParse(value: unknown) { return {success: true, data: value}; }};", "import {Schema} from './never_execute';"),
        "raw_sink": ("setRows(result.data)", "setRows(raw)"),
        "wrong_guard": ("if (result.success)", "if (other.success)"),
        "negated": ("if (result.success)", "if (!result.success)"),
        "else": ("if (result.success) { setRows(result.data); }", "if (!result.success) {} else { setRows(result.data); }"),
        "comparison": ("if (result.success)", "if (result.success === true)"),
        "deferred": ("setRows(result.data);", "queueMicrotask(() => setRows(result.data));"),
        "reassigned": ("const result =", "let result ="),
        "shadow_setter": ("async function load()", "async function load(setRows: (v: unknown) => void)"),
        "hook_impostor": ("import {useState} from 'react';", "const useState = <T,>(v:T) => [v, (x:unknown) => x] as const;"),
        "awaited": ("Schema.safeParse(raw)", "await Schema.safeParse(raw)"),
        "async_protocol": ("Schema.safeParse(raw)", "Schema.safeParseAsync(raw)"),
        "data_alias": ("setRows(result.data);", "const data = result.data; setRows(data);"),
        "extra_branch": ("setRows(result.data);", "setRows(result.data); audit();"),
        "trailing": ("if (result.success) { setRows(result.data); }", "if (result.success) { setRows(result.data); } audit();"),
        "gap": ("const result =", "audit(); const result ="),
        "optional_guard": ("result.success", "result?.success"),
        "optional_call": ("Schema.safeParse(raw)", "Schema.safeParse?.(raw)"),
        "optional_json": ("response.json()", "response?.json()"),
        "optional_setter": ("setRows(result.data)", "setRows?.(result.data)"),
        "result_cast": ("Schema.safeParse(raw)", "Schema.safeParse(raw) as any"),
        "data_cast": ("setRows(result.data)", "setRows(result.data as any)"),
        "element_guard": ("result.success", "result['success']"),
        "element_data": ("result.data", "result['data']"),
        "wrong_input": ("Schema.safeParse(raw)", "Schema.safeParse(other)"),
        "local_fetch": ("async function load()", "async function load(fetch: (url: string) => unknown)"),
        "namespace_hook": ("import {useState} from 'react';", "import * as React from 'react';"),
        "any_state": ("useState<string[]>", "useState<any>"),
        "type_only": ("import {useState}", "import type {useState}"),
        "arrow_body": ("async function load()", "const load = async () =>"),
        "duplicate": ("const raw =", "const raw = 0; const raw ="),
        "hoisted_shadow": ("const result =", "if (opaque) { var result; } const result ="),
    }
    for name, (before, after) in replacements.items():
        text = source.replace(before, after)
        if name == "alias":
            text = text.replace("= useState<", "= stateHook<").replace("result", "parsed").replace("setRows", "updateRows")
        if name == "namespace_hook":
            text = text.replace("= useState<", "= React.useState<")
        cases[name] = (text, int(name == "alias"))
    for name, write in {"owner_write": "Schema.safeParse = replacement;", "raw_write": "raw.push(1); raw.x = 1;",
            "response_write": "response.json = replacement;", "setter_write": "setRows = replacement;",
            "result_write": "result.data = other;", "delete": "delete Schema.safeParse;",
            "destructure_write": "({slot: setRows} = opaque);", "update": "result.success++;"}.items():
        # A closure before the tail still binds the same selected variables.
        # An unrelated module-level unresolved name would not be a write to them.
        cases[name] = (source.replace("  const response =", "  function later() { " + write + " }\n  const response ="), 0)
    cases["unicode"] = ("\ufeff// 😀 canonical bytes\r\n" + source.replace("\n", "\r\n"), 1)
    state = " const [rows, setRows] = useState<string[]>([]);\n"
    cases["state_after_sink"] = (source.replace(state, "").replace(" return <button", state + " return <button"), 0)
    hook_import = "import {useState} from 'react';\n"
    cases["import_after_state"] = (source.replace(hook_import, "") + hook_import, 0)
    cases["diagnostics"] = (source + "const broken = ;", 0)
    cases["byte_budget"] = (source + "/*" + "x" * 1048576 + "*/", 0)
    cases["node_budget"] = (source + "\n" + "\n".join(f"const v{i} = {i};" for i in range(2600)), 0)
    # One shared imported hook and parser owner; each selected binding is distinct.
    functions = source[source.index("export function Panel"):]
    parts = []
    for i in range(65):
        part = functions
        for token in ("Panel", "load", "rows", "setRows", "response", "raw", "result"):
            part = part.replace(token, token + str(i))
        parts.append(part)
    cases["record_budget"] = (source[:source.index("export function Panel")] + "\n".join(parts), 64)
    return cases


@pytest.fixture(scope="module")
def safeparse_native(tmp_path_factory):
    if not shutil.which("node"):
        pytest.skip("Node is required for actual native AST controls")
    root = Path(__file__).resolve().parents[2]
    directory = tmp_path_factory.mktemp("safeparse-native")
    cases = _safeparse_cases()
    paths = []
    for name, (source, _) in cases.items():
        path = directory / (name + ".tsx")
        path.write_bytes(source.encode("utf-8"))
        paths.append(str(path))
    (directory / "never_execute.ts").write_text("throw new Error('TARGET_MUST_NOT_EXECUTE');", encoding="utf-8")
    result = subprocess.run(["node", str(root / "tools/engines/ast_sequencer.cjs"), "--batch-json", *paths],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    return {name: (source, payload[str(directory / (name + '.tsx'))], count) for name, (source, count) in cases.items()}


@pytest.mark.parametrize("name", list(_safeparse_cases()))
def test_safeparse_success_native_finite_grammar(name, safeparse_native):
    import hashlib
    from tools.engines.generate_atlas import _client_safeparse_success_evidence
    source, native, expected = safeparse_native[name]
    meta = next(row for row in native if row["name"] == "__file_meta__")
    lane = meta["clientSafeParseSuccessEvidence"]
    assert lane["record_count"] == len(lane["records"]) == expected, (name, lane)
    assert lane["source_hash"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
    evidence = _client_safeparse_success_evidence(native, source)
    assert len(evidence["records"]) == expected, (name, evidence)
    if name in {"diagnostics", "byte_budget", "node_budget"}:
        assert evidence["status"] == "unavailable" and evidence["reason"]
    elif name == "record_budget":
        assert evidence["status"] == "partial" and evidence["omitted"] == 1
    else:
        assert evidence["status"] == "observed" and evidence["omitted"] == 0
    assert evidence["schema_authority"] == evidence["type_compatibility"] == "not_established"
    assert evidence["runtime_proof"] == "not_performed" and evidence["mutation_proposed"] is False
    if name == "unicode":
        encoded = source.encode("utf-8")
        spans = evidence["records"][0]["spans"]
        for span in spans.values():
            assert encoded[span["start"]:span["end"]].decode("utf-8")
        assert encoded[spans["success_property"]["start"]:spans["success_property"]["end"]] == b"success"
        assert encoded[spans["data_property"]["start"]:spans["data_property"]["end"]] == b"data"
    # This optional source lane cannot add ordinary advisory findings.
    result = analyze_runtime_intelligence_file("fixture", "Panel.tsx", source,
        atlas_file={"features": meta["features"], "symbols": native, "client_safeparse_success_evidence": evidence})
    assert not any(row["dimension"] == "target_runtime_schema_mutation" for row in result["findings"])


@pytest.mark.parametrize("damage", ["missing", "version", "hash", "atlas_hash", "meta_duplicate", "degraded",
    "count", "bool_count", "role", "extra_role", "bool_offset", "range", "line", "relation", "claim",
    "header_claim", "duplicate", "partial", "omitted", "unknown_status", "unavailable_rows"])
def test_safeparse_success_transport_refuses_corrupt_or_legacy(damage, safeparse_native):
    from copy import deepcopy
    from tools.engines.generate_atlas import _client_safeparse_success_evidence
    source, raw, _ = safeparse_native["positive"]
    native = deepcopy(raw)
    meta = next(row for row in native if row["name"] == "__file_meta__")
    lane = meta["clientSafeParseSuccessEvidence"]
    row = lane["records"][0]
    if damage == "missing": meta.pop("clientSafeParseSuccessEvidence")
    elif damage == "version": lane["version"] = "old"
    elif damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "atlas_hash": meta["features"].append("Hash:" + "0" * 64)
    elif damage == "meta_duplicate": native.append(deepcopy(meta))
    elif damage == "degraded": meta["parserStatus"] = "degraded"
    elif damage == "count": lane["record_count"] = 2
    elif damage == "bool_count": lane["record_count"] = True
    elif damage == "role": row["spans"].pop("condition_result_ref")
    elif damage == "extra_role": row["spans"]["validated"] = row["spans"]["sink"]
    elif damage == "bool_offset": row["spans"]["sink"]["start"] = True
    elif damage == "range": row["spans"]["sink"]["end"] = len(source) * 4
    elif damage == "line": row["spans"]["sink"]["line"] += 1
    elif damage == "relation": row["spans"]["condition_result_ref"] = row["spans"]["result_binding"]
    elif damage == "claim": row["validated"] = True
    elif damage == "header_claim": lane["schema_authority"] = "validated"
    elif damage == "duplicate": lane["records"].append(deepcopy(row)); lane["record_count"] = 2
    elif damage == "partial": lane["status"] = "partial"; lane["reason"] = "record_budget_exceeded"; lane["omitted"] = 1
    elif damage == "omitted": lane["omitted"] = True
    elif damage == "unknown_status": lane["status"] = []
    else: lane["status"] = "unavailable"; lane["reason"] = "node_budget_exceeded"
    result = _client_safeparse_success_evidence(native, source)
    assert result["status"] == "unavailable" and result["records"] == [] and result["reason"]


def test_safeparse_success_reuses_shared_file_coordinate_batch(safeparse_native, monkeypatch):
    from tools.engines import generate_atlas as atlas
    source, native, _ = safeparse_native["unicode"]
    context = {}
    atlas._normalize_polyglot_symbols(native, source, "typescript", file_evidence_context=context)
    def forbid(*args, **kwargs):
        raise AssertionError("No second coordinate/index scan in production source-lane transport")
    monkeypatch.setattr(atlas, "_utf16_offsets_to_utf8_byte_offsets", forbid)
    monkeypatch.setattr(atlas, "_native_utf16_span_validator", forbid)
    assert atlas._client_safeparse_success_evidence(native, source, context)["record_count"] == 1


def test_safeparse_success_empty_lane_needs_no_coordinate_index(safeparse_native, monkeypatch):
    from tools.engines import generate_atlas as atlas
    source, native, _ = safeparse_native["empty"]
    def forbid(*args, **kwargs):
        raise AssertionError("Observed empty must not build a source coordinate index")
    monkeypatch.setattr(atlas, "_utf16_offsets_to_utf8_byte_offsets", forbid)
    monkeypatch.setattr(atlas, "_native_utf16_span_validator", forbid)
    assert atlas._client_safeparse_success_evidence(native, source)["status"] == "observed"


@pytest.mark.parametrize("name", ["positive", "empty", "record_budget", "byte_budget"])
def test_safeparse_success_schema_discloses_coverage_and_refuses_claims(name, safeparse_native, tmp_path):
    from tools.core.artifact_validator import validate_against_schema
    from tools.engines.generate_atlas import _client_safeparse_success_evidence
    source, native, _ = safeparse_native[name]
    evidence = _client_safeparse_success_evidence(native, source)
    schema = json.loads((Path(__file__).resolve().parents[2] / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    selected_schema = tmp_path / "selected-schema.json"
    selected_schema.write_text(json.dumps({"$ref": "#/$defs/client_safeparse_success_evidence", "$defs": schema["$defs"]}), encoding="utf-8")
    def errors(lane):
        return validate_against_schema(selected_schema, "source_lane", lane)
    assert errors(evidence) == []
    assert errors({**evidence, "mutation_proposed": 0})
    assert errors({**evidence, "schema_authority": "validated"})
    assert errors({**evidence, "unproved_claim": True})


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
    (target / "Safe.tsx").write_bytes(_safeparse_source().encode("utf-8"))
    (target / "Effect.tsx").write_bytes(_effect_safeparse_source().encode("utf-8"))
    (target / "Merge.tsx").write_bytes(_effect_merge_source().encode("utf-8"))
    (target / "Early.tsx").write_bytes(_early_return_source().encode("utf-8"))
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
    loaded = load_atlas_data(raw)
    files = loaded["MAIN"]["files"]
    effect = files["Effect.tsx"]["effect_safeparse_callback_evidence"]
    assert effect["status"] == "observed" and effect["record_count"] == 1
    assert effect["schema_authority"] == "not_established" and effect["mutation_proposed"] is False
    assert files["Safe.tsx"]["effect_safeparse_callback_evidence"]["record_count"] == 0
    merge = files["Merge.tsx"]["effect_safeparse_merge_evidence"]
    assert merge["status"] == "observed" and merge["record_count"] == 1
    assert merge["merge_result_semantics"] == "not_established"
    assert files["Merge.tsx"]["effect_safeparse_callback_evidence"]["record_count"] == 0
    assert files["Effect.tsx"]["effect_safeparse_merge_evidence"]["record_count"] == 0
    early = files["Early.tsx"]["safeparse_early_return_evidence"]
    assert early["status"] == "observed" and early["record_count"] == 1
    assert early["factory_semantics"] == early["continuation_safety"] == "not_established"
    assert files["Early.tsx"]["effect_safeparse_callback_evidence"]["record_count"] == 0
    assert files["Early.tsx"]["effect_safeparse_merge_evidence"]["record_count"] == 0
    assert files["Effect.tsx"]["safeparse_early_return_evidence"]["record_count"] == 0
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
    safe = files["Safe.tsx"]
    evidence = safe["client_safeparse_success_evidence"]
    assert evidence["status"] == "observed" and evidence["record_count"] == 1
    assert evidence["source_hash"] == safe["hash"] and evidence["ast_contract_version"] == safe["ast_contract_version"]
    assert evidence["runtime_proof"] == "not_performed" and evidence["schema_authority"] == "not_established"
    assert safe["client_local_parser_result_evidence"]["records"] == []
    from tools.core.artifact_validator import validate_payload
    assert validate_payload("atlas", loaded) == []
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


def _factory_return_source():
    return "import { z } from 'zod';\nexport const makeEmail = (options?: string) => {\n const message = options ?? 'Invalid';\n return z.string().regex(PATTERN, { message });\n};\n"


def _factory_function_source(source):
    return source.replace("export const makeEmail = (options?: string) =>", "export function makeEmail(options?: string)").replace("};\n", "}\n", 1)


def _factory_parameter_sources():
    caller = _early_return_source().replace("const complete", "const options = acquireOpaque();\nconst complete").replace("schemaFactory()", "schemaFactory(options)")
    cases = {"caller":(caller,"caller",True),"caller_unicode":("\ufeff// 😀\r\n"+caller.replace("\n","\r\n"),"caller",True),
        "caller_comment":(caller.replace("schemaFactory(options)","schemaFactory(/*arg*/ options /*end*/)"),"caller",True),
        "caller_component":(caller.replace("const {email}","export function Component() {\nconst {email}")+"}\n","caller",True)}
    changes = {"literal":("schemaFactory(options)","schemaFactory('value')"),"spread":("schemaFactory(options)","schemaFactory(...options)"),
        "two":("schemaFactory(options)","schemaFactory(options, options)"),"trailing":("schemaFactory(options)","schemaFactory(options,)"),
        "property":("schemaFactory(options)","schemaFactory(options.value)"),"expression":("schemaFactory(options)","schemaFactory(acquireOpaque())"),
        "mutable":("const options =","let options ="),"typed":("const options =","const options: unknown ="),
        "multiple":("const options = acquireOpaque();","const options = acquireOpaque(), other = 1;"),
        "late":("const options = acquireOpaque();\n",""),"assigned":("const complete", "options = other;\nconst complete"),
        "property_write":("const complete","options.value = other;\nconst complete"),"shadow":("const complete","function other(options) {}\nconst complete"),
        "destructured":("const options = acquireOpaque();","const {options} = acquireOpaque();"),
        "nested":("const options = acquireOpaque();\nconst complete", "const complete"),
        "type_import":("import {schemaFactory}","import type {schemaFactory}"),
        "escaped_reference":("schemaFactory(options)","schemaFactory(opt\\u0069ons)"),
        "escaped_declaration":("const options =","const opt\\u0069ons ="),
        "optional_call":("schemaFactory(options)","schemaFactory?.(options)")}
    for name,(a,b) in changes.items(): cases["caller_"+name]=(caller.replace(a,b),"caller",False)
    cases["caller_late"]=(cases["caller_late"][0]+"const options = acquireOpaque();\n","caller",False)
    cases["caller_nested"]=(cases["caller_nested"][0].replace(" try {"," const options = acquireOpaque();\n try {"),"caller",False)
    formal = "import {z} from 'never-execute';\nexport const make = (settings) => z.string().regex(settings);\nthrow new Error('MUST_NOT_EXECUTE');"
    cases.update({"formal":(formal,"formal",True),"formal_bare":(formal.replace("(settings)","settings",1),"formal",True),
        "formal_typed":(formal.replace("(settings)","(settings: {value: string})",1),"formal",True),
        "formal_function":(formal.replace("export const make = (settings) => z.string().regex(settings);","export function make(settings) { return z.string().regex(settings); }"),"formal",True),
        "formal_unicode":("\ufeff// 😀\r\n"+formal.replace("\n","\r\n"),"formal",True)})
    exclusions={"default":"(settings = opaque())","optional":"(settings?: unknown)","rest":"(...settings)","destructured":"({settings})", "multiple":"(settings, other)","trailing":"(settings,)"}
    for name,signature in exclusions.items(): cases["formal_"+name]=(formal.replace("(settings)",signature,1),"formal",False)
    cases["formal_written"]=(formal.replace("throw new Error", "settings = other; throw new Error").replace("=> z.string().regex(settings);", "=> { settings = other; return z.string().regex(settings); };"),"formal",False)
    cases["formal_shadow"]=(formal+"\nfunction unrelated(settings) {}","formal",False)
    cases["formal_this"]=("import {z} from 'never-execute'; export function make(this: unknown) { return z.string(); }","formal",False)
    cases["formal_escaped"]=(formal.replace("(settings)","(set\\u0074ings)",1),"formal",False)
    return cases


def _factory_parameter_use_sources():
    source="import {z} from 'never-execute';\nexport const make = (settings: unknown) => z.min(settings).max(settings, other).refine(other, settings);\nthrow new Error('MUST_NOT_EXECUTE');"
    cases={"arrow":(source,3),"unicode":("\ufeff// 😀\r\n"+source.replace("\n","\r\n"),3),
        "function":(source.replace("export const make = (settings: unknown) =>", "export function make(settings: unknown) { return").replace(";\nthrow", "; }\nthrow"),3),
        "bare":(source.replace("(settings: unknown)","settings"),3),
        "comments":(source.replace(".min(settings)",".min(/*name*/settings/*end*/)"),3)}
    prefix="import {z} from 'never-execute';\nexport const make = (settings) => "
    expressions={"repeated":("z.regex(settings,settings)",2),"unused":("z.regex(other)",0),
        "property":("z.regex(settings.value)",0),"shorthand":("z.regex({settings})",0),
        "nested":("z.refine(x => settings)",0),"wrapped":("z.regex(opaque(settings))",0),
        "spread":("z.regex(settings,...other)",0),"trailing":("z.regex(settings,)",0),
        "five_arguments":("z.regex(settings,1,2,3,4)",0),"escaped":("z.regex(set\\u0074ings)",0),
        "alias":("{ const alias=settings; return z.regex(alias); }",0)}
    cases.update({name:(prefix+expression+";",count) for name,(expression,count) in expressions.items()})
    factories=[]
    for i in range(9):
        factories.append(f"export const make{i} = (settings{i}) => z."+".".join(f"m{j}(settings{i})" for j in range(8))+";")
    cases["budget"]=("import {z} from 'never-execute';\n"+"\n".join(factories),64)
    return cases


@pytest.fixture(scope="module")
def factory_parameter_use_native(tmp_path_factory):
    directory=tmp_path_factory.mktemp("factory-parameter-use-source")
    cases=_factory_parameter_use_sources()
    paths=[]
    for name,(source,_) in cases.items():
        path=directory/(name+".tsx")
        path.write_bytes(source.encode("utf-8")); paths.append(str(path))
    root=Path(__file__).resolve().parents[2]
    run=subprocess.run(["node",str(root/"tools/engines/ast_sequencer.cjs"),"--batch-json",*paths],cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=90)
    assert run.returncode==0,run.stderr
    native=json.loads(run.stdout)
    return {name:(source,native[path],expected) for (name,(source,expected)),path in zip(cases.items(),paths)}


@pytest.mark.parametrize("name",list(_factory_parameter_use_sources()))
def test_factory_parameter_use_native_transport_and_schema(factory_parameter_use_native,tmp_path,monkeypatch,name):
    from tools.engines import generate_atlas as atlas
    from tools.core.artifact_validator import validate_against_schema
    source,native,expected=factory_parameter_use_native[name]
    context={}
    atlas._normalize_polyglot_symbols(native,source,"typescript",file_evidence_context=context)
    def forbid(*a,**k): raise AssertionError("Reuse the one file coordinate batch")
    monkeypatch.setattr(atlas,"_utf16_offsets_to_utf8_byte_offsets",forbid)
    monkeypatch.setattr(atlas,"_native_utf16_span_validator",forbid)
    result=atlas._factory_return_construction_evidence(native,source,context)
    assert result["status"]=="observed",result
    assert sum(len(r.get("parameter_uses",[])) for r in result["records"])==expected,result
    assert sum(r.get("parameter_uses_omitted",0) for r in result["records"])==(8 if name=="budget" else 0)
    if name=="budget":
        assert result["record_count"]==9 and result["omitted"]==0
    encoded=source.encode("utf-8")
    for row in result["records"]:
        for index,use in enumerate(row.get("parameter_uses",[])):
            span=row["spans"][f"parameter_use_{index}"]
            assert encoded[span["start"]:span["end"]].decode("utf-8")==row["parameter_name"]
    if name=="arrow":
        assert [(r["call_index"],r["argument_index"]) for r in result["records"][0]["parameter_uses"]]==[(0,0),(1,0),(2,1)]
    schema=json.loads((Path(__file__).resolve().parents[2]/"config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    path=tmp_path/"schema.json"
    path.write_text(json.dumps({"$ref":"#/$defs/factory_return_construction_evidence","$defs":schema["$defs"]}),encoding="utf-8")
    assert validate_against_schema(path,"factory",result)==[]


@pytest.mark.parametrize("damage",["missing_uses","extra_field","boolean_index","wrong_index","wrong_call","wrong_reference","argument_boundary","missing_role","extra_role","negative_omitted","boolean_omitted","false_budget","hidden_row","duplicate_use"])
def test_factory_parameter_use_refuses_corrupt_transport(factory_parameter_use_native,damage):
    from copy import deepcopy
    from tools.engines import generate_atlas as atlas
    source,native,_=factory_parameter_use_native["budget" if damage=="hidden_row" else "arrow"]
    native=deepcopy(native)
    lane=next(r for r in native if r["name"]=="__file_meta__")["factoryReturnConstructionEvidence"]
    row=lane["records"][-1 if damage=="hidden_row" else 0]
    if damage=="missing_uses": row.pop("parameter_uses")
    elif damage=="extra_field": row["parameter_uses"][0]["effects"]="proved"
    elif damage=="boolean_index": row["parameter_uses"][0]["argument_index"]=True
    elif damage=="wrong_index": row["parameter_uses"][1]["argument_index"]=1
    elif damage=="wrong_call": row["parameter_uses"][0]["call_index"]=2
    elif damage=="wrong_reference": row["spans"]["parameter_use_0"]=row["spans"]["parameter_binding"]
    elif damage=="argument_boundary": row["spans"]["use_0_argument_0"]["end"]+=1
    elif damage=="missing_role": row["spans"].pop("parameter_use_0")
    elif damage=="extra_role": row["spans"]["parameter_use_999"]=row["spans"]["parameter_use_0"]
    elif damage=="negative_omitted": row["parameter_uses_omitted"]=-1
    elif damage=="boolean_omitted": row["parameter_uses_omitted"]=True
    elif damage=="false_budget": row["parameter_uses_omitted"]=1
    elif damage=="hidden_row": row["parameter_uses_omitted"]=-1
    elif damage=="duplicate_use":
        index=len(row["parameter_uses"])
        row["parameter_uses"].append(deepcopy(row["parameter_uses"][0]))
        row["spans"][f"parameter_use_{index}"]=deepcopy(row["spans"]["parameter_use_0"])
        row["spans"][f"use_{index}_argument_0"]=deepcopy(row["spans"]["use_0_argument_0"])
    result=atlas._factory_return_construction_evidence(native,source)
    assert result["status"]=="unavailable" and not result["records"],result


@pytest.fixture(scope="module")
def factory_parameter_native(tmp_path_factory):
    directory=tmp_path_factory.mktemp("factory-parameter-source")
    cases=_factory_parameter_sources()
    paths=[]
    for name,(source,_,_) in cases.items():
        path=directory/(name+".tsx")
        path.write_bytes(source.encode("utf-8"))
        paths.append(str(path))
    root=Path(__file__).resolve().parents[2]
    run=subprocess.run(["node",str(root/"tools/engines/ast_sequencer.cjs"),"--batch-json",*paths],cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=90)
    assert run.returncode==0,run.stderr
    native=json.loads(run.stdout)
    return {name:(source,native[path],kind,expected) for (name,(source,kind,expected)),path in zip(cases.items(),paths)}


@pytest.mark.parametrize("name",list(_factory_parameter_sources()))
def test_factory_parameter_native_source_and_transport(factory_parameter_native,name,tmp_path,monkeypatch):
    from tools.engines import generate_atlas as atlas
    from tools.core.artifact_validator import validate_against_schema
    source,native,kind,expected=factory_parameter_native[name]
    context={}
    atlas._normalize_polyglot_symbols(native,source,"typescript",file_evidence_context=context)
    def forbid(*a,**k): raise AssertionError("No second coordinate map")
    monkeypatch.setattr(atlas,"_utf16_offsets_to_utf8_byte_offsets",forbid)
    monkeypatch.setattr(atlas,"_native_utf16_span_validator",forbid)
    lane="safeparse_early_return_evidence" if kind=="caller" else "factory_return_construction_evidence"
    evidence=(atlas._safeparse_early_return_evidence if kind=="caller" else atlas._factory_return_construction_evidence)(native,source,context)
    assert evidence["status"]=="observed",(name,evidence)
    rows=evidence["records"]
    assert bool(rows and (kind=="caller" or "parameter_name" in rows[0])) is expected,(name,evidence)
    schema=json.loads((Path(__file__).resolve().parents[2]/"config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    path=tmp_path/"schema.json"
    path.write_text(json.dumps({"$ref":"#/$defs/"+lane,"$defs":schema["$defs"]}),encoding="utf-8")
    assert validate_against_schema(path,"source",evidence)==[],(name,evidence)
    if expected:
        row=rows[0]
        role="factory_argument" if kind=="caller" else "parameter_binding"
        spelling="options" if kind=="caller" else "settings"
        assert source.encode("utf-8")[row["spans"][role]["start"]:row["spans"][role]["end"]].decode("utf-8")==spelling


@pytest.mark.parametrize("kind",["caller","formal"])
@pytest.mark.parametrize("damage",["name","missing_role","extra_role","coordinate","version","arity","field","claim"])
def test_factory_parameter_transport_refusals(factory_parameter_native,kind,damage):
    from copy import deepcopy
    from tools.engines import generate_atlas as atlas
    source,native,_,_=factory_parameter_native[kind]
    native=deepcopy(native)
    meta=next(r for r in native if r["name"]=="__file_meta__")
    lane=meta["safeParseEarlyReturnEvidence" if kind=="caller" else "factoryReturnConstructionEvidence"]
    row=lane["records"][0]
    role="factory_argument" if kind=="caller" else "parameter_binding"
    name="factory_argument_name" if kind=="caller" else "parameter_name"
    if damage=="name": row[name]="other"
    elif damage=="missing_role": row["spans"].pop(role)
    elif damage=="extra_role": row["spans"]["unknown"]=row["spans"][role]
    elif damage=="coordinate": row["spans"][role]["start"]+=1
    elif damage=="version": lane["version"]="old"
    elif damage=="arity":
        if kind=="caller": row["spans"]["factory_argument"]["end"]+=1
        else: row["spans"]["parameters_close"]=row["spans"]["parameters_open"]
    elif damage=="field": row["value"]="not source authority"
    elif damage=="claim": lane["runtime_proof"]="passed"
    result=(atlas._safeparse_early_return_evidence if kind=="caller" else atlas._factory_return_construction_evidence)(native,source)
    assert result["status"]=="unavailable" and result["records"]==[],result


@pytest.mark.parametrize("kind",["caller","formal"])
@pytest.mark.parametrize("damage",["boolean_name","missing_name","missing_span","bad_form","non_mapping_spans","this_parameter"])
def test_factory_parameter_schema_and_stored_refusals(factory_parameter_native,tmp_path,kind,damage):
    from copy import deepcopy
    from tools.engines import generate_atlas as atlas
    from tools.core.artifact_validator import validate_against_schema
    from tools.core.factory_return_contract import factory_return_stored_problem
    from tools.core.safeparse_early_return_contract import early_return_stored_problem
    from tools.core.source_coordinates import _utf8_span_validator
    source,native,_,_=factory_parameter_native[kind]
    lane="safeparse_early_return_evidence" if kind=="caller" else "factory_return_construction_evidence"
    result=(atlas._safeparse_early_return_evidence if kind=="caller" else atlas._factory_return_construction_evidence)(native,source)
    validator=_utf8_span_validator(source,[s for row in result["records"] for s in row["spans"].values()])
    changed=deepcopy(result)
    row=changed["records"][0]
    name="factory_argument_name" if kind=="caller" else "parameter_name"
    if damage=="boolean_name": row[name]=True
    elif damage=="missing_name": row.pop(name)
    elif damage=="missing_span": row["spans"].pop("factory_argument" if kind=="caller" else "parameter_binding")
    elif damage=="bad_form":
        if kind=="caller": row["spans"]["not_a_role"]={}
        else: row["parameter_form"]="unknown"
    elif damage=="non_mapping_spans": row["spans"]=None
    elif damage=="this_parameter":
        if kind=="caller": row[name]="not an identifier"
        else: row[name]="this"
    guard=early_return_stored_problem if kind=="caller" else factory_return_stored_problem
    assert guard(changed,result["source_hash"],validator,ast_contract_version=atlas.AST_CONTRACT_VERSION)
    schema=json.loads((Path(__file__).resolve().parents[2]/"config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    path=tmp_path/"schema.json"
    path.write_text(json.dumps({"$ref":"#/$defs/"+lane,"$defs":schema["$defs"]}),encoding="utf-8")
    assert validate_against_schema(path,"changed",changed)


def _factory_return_cases():
    source = _factory_return_source()
    cases = {"block": (source, 1), "expression": ("import {z} from 'zod'; export const make = () => z.object({id: z.string()}).optional();", 1),
        "alias": (source.replace("{ z }", "{ z as schema }").replace("z.string()", "schema.string()"), 1),
        "unicode": ("\ufeff// 😀\r\n" + source.replace("\n", "\r\n"), 1),
        "unknown_library": (source.replace("'zod'", "'arbitrary-uninstalled-library'"), 1),
        "empty": ("export const ordinary = () => 1;", 0)}
    changes = {"type_only": ("{ z }", "{ type z }"), "type_clause": ("import {", "import type {"),
        "namespace": ("{ z }", "* as z"), "default": ("{ z }", "z"), "local_root": ("import { z } from 'zod';", "const z = other;"),
        "not_exported": ("export const", "const"), "let": ("export const", "export let"), "async": ("= (options", "= async (options"),
        "generic": ("z.string()", "z.string<string>()"), "optional": ("z.string()", "z?.string()"),
        "computed": ("z.string()", "z['string']()"), "cast": ("z.string()", "(z as any).string()"),
        "indirect": ("z.string()", "other(z).string()"),
        "wrapped_return": ("return z.string().regex(PATTERN, { message });", "return (z.string().regex(PATTERN, { message }));"),
        "conditional": ("return z.string().regex(PATTERN, { message });", "return options ? z.string() : z.number();"),
        "branch": ("return z.string().regex(PATTERN, { message });", "if (options) return z.string(); return z.number();"),
        "prefix_let": ("const message", "let message"), "later_statement": ("{ message });", "{ message }); notify();"),
        "too_many_parameters": ("(options?: string)", "(a,b,c,d,e)"),
        "rest": ("(options?: string)", "(...options: unknown[])"),
        "chain_budget": ("z.string()", "z.a().b().c().d().e().f().g().h().i()")}
    cases.update({name: (source.replace(*change), 0) for name,change in changes.items()})
    cases.update(root_write=(source + "z.string = other;", 0), factory_write=(source + "makeEmail = other;", 0),
        root_destructuring_write=(source + "({z} = other);", 0),
        shadow=(source + "function other(z: unknown) {}", 0), import_after=(source.replace("import { z } from 'zod';", "") + "import { z } from 'zod';", 0),
        diagnostics=("import {", 0), byte_budget=(source + "/*" + "x" * 1048576 + "*/", 0),
        node_budget=(source + "const huge = [" + ",".join("0" for _ in range(11000)) + "];", 0),
        record_budget=("import {z} from 'zod';\n" + "\n".join(f"export const make{i} = () => z.string();" for i in range(65)), 64))
    function = _factory_function_source(source)
    cases.update(function=(function,1),function_unicode=("\ufeff// 😀\r\n"+function.replace("\n","\r\n"),1),
        function_alias=(function.replace("{ z }","{ z as schema }").replace("z.string()","schema.string()"),1),
        function_comment=(function.replace("export function", "export /* source */ function"),1))
    functions = {"not_exported":("export function","function"),"default":("export function","export default function"),
        "async":("export function","export async function"),"generator":("function makeEmail","function* makeEmail"),
        "type_parameter":("makeEmail(options", "makeEmail<T>(options"),"rest":("options?: string","...options: unknown[]"),
        "destructured_parameter":("options?: string","{options}: unknown"),
        "branch":("return z.string().regex(PATTERN, { message });","if(options) return z.string(); return z.number();"),
        "type_only":("{ z }","{ type z }"),"nested":("export function makeEmail", "function outer() { function makeEmail")}
    cases.update({"function_"+name:(function.replace(a,b)+( "}" if name=="nested" else ""),0) for name,(a,b) in functions.items()})
    cases.update(function_overload=(function.replace("export function makeEmail", "export function makeEmail(value: string): unknown;\nexport function makeEmail"),0),
        function_write=(function+"makeEmail = other;",0),function_shadow=(function+"function other(makeEmail: unknown) {}",0),
        function_expression=(source.replace("(options?: string) =>", "function(options?: string)"),0),
        function_record_budget=("import {z} from 'zod';\n"+"\n".join(f"export function make{i}() {{ return z.string(); }}" for i in range(65)),64))
    return cases


@pytest.fixture(scope="module")
def factory_return_native(tmp_path_factory):
    cases = _factory_return_cases()
    root = Path(__file__).resolve().parents[2]
    directory = tmp_path_factory.mktemp("factory-return-source")
    paths = []
    for name,(source, _) in cases.items():
        path = directory / (name + ".tsx")
        path.write_bytes(source.encode("utf-8"))
        paths.append(str(path))
    process = subprocess.run(["node", str(root / "tools/engines/ast_sequencer.cjs"), "--batch-json", *paths],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert process.returncode == 0, process.stderr
    native = json.loads(process.stdout)
    return {name:(source, native[path], expected) for (name,(source,expected)),path in zip(cases.items(), paths)}


@pytest.mark.parametrize("name", list(_factory_return_cases()))
def test_factory_return_source_and_transport(name, factory_return_native):
    from tools.engines import generate_atlas as atlas
    from tools.core.factory_return_contract import factory_return_record_valid
    from tools.core.source_coordinates import _utf8_span_validator
    source, native, expected = factory_return_native[name]
    lane = next(row for row in native if row["name"] == "__file_meta__")["factoryReturnConstructionEvidence"]
    assert lane["record_count"] == expected, (name, lane)
    normalized = atlas._factory_return_construction_evidence(native, source)
    assert normalized["record_count"] == expected, (name, normalized)
    assert normalized["factory_semantics"] == normalized["argument_semantics"] == normalized["schema_authority"] == "not_established"
    assert normalized["runtime_proof"] == "not_performed" and normalized["mutation_proposed"] is False
    assert normalized["status"] == ("unavailable" if name in {"diagnostics", "byte_budget", "node_budget"} else "partial" if name in {"record_budget","function_record_budget"} else "observed")
    for row in normalized["records"]:
        assert factory_return_record_valid(row, _utf8_span_validator(source, row["spans"].values()))
    if expected and name not in {"record_budget","function_record_budget"}:
        assert normalized["records"][0]["methods"] == (["object", "optional"] if name == "expression" else ["string", "regex"])


@pytest.mark.parametrize("damage", ["missing", "hash", "version", "parser", "count", "bool_count", "omitted", "extra", "factory", "method", "module", "type_only", "body", "span", "open", "order", "duplicate", "hidden", "function_kind", "function_binding", "function_keyword", "function_body", "missing_kind"])
def test_factory_return_transport_refuses_corruption(factory_return_native, damage):
    from copy import deepcopy
    from tools.engines import generate_atlas as atlas
    source, original, _ = factory_return_native["record_budget" if damage == "hidden" else "function" if damage.startswith("function_") else "block"]
    raw = deepcopy(original)
    meta = next(row for row in raw if row["name"] == "__file_meta__")
    lane = meta["factoryReturnConstructionEvidence"]
    record = lane["records"][-1 if damage == "hidden" else 0]
    if damage == "missing": meta.pop("factoryReturnConstructionEvidence")
    elif damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "version": lane["version"] = "old"
    elif damage == "parser": meta["parserStatus"] = "degraded"
    elif damage == "count": lane["record_count"] += 1
    elif damage == "bool_count": lane["record_count"] = True
    elif damage == "omitted": lane["omitted"] = 1
    elif damage == "extra": record["schema_validated"] = True
    elif damage == "factory": record["factory_name"] = "other"
    elif damage == "method": record["methods"][0] = "number"
    elif damage == "module": record["import_binding"]["source"] = "other"
    elif damage == "type_only": record["import_binding"]["type_only"] = True
    elif damage == "body": record["body_kind"] = "expression"
    elif damage in {"span", "hidden"}: record["spans"]["root"]["line"] += 1
    elif damage == "open": record["spans"]["open_0"] = record["spans"]["close_0"]
    elif damage == "order": record["spans"]["method_0"] = record["spans"]["method_1"]
    elif damage == "duplicate": lane["records"] *= 2; lane["record_count"] = 2
    elif damage == "function_kind": record["factory_kind"] = "const_arrow"
    elif damage == "missing_kind": record.pop("factory_kind")
    elif damage == "function_binding": record["spans"]["factory_binding"] = record["spans"]["root"]
    elif damage == "function_keyword": record["spans"]["factory"]["start"] += 1
    elif damage == "function_body": record["body_kind"] = "expression"
    result = atlas._factory_return_construction_evidence(raw, source)
    assert result["status"] == "unavailable" and result["records"] == [] and result["reason"]


@pytest.mark.parametrize("name", ["block", "expression", "unicode", "empty", "record_budget", "node_budget","function","function_unicode","function_record_budget"])
def test_factory_return_schema_and_shared_coordinate_batch(factory_return_native, tmp_path, monkeypatch, name):
    from tools.engines import generate_atlas as atlas
    from tools.core.artifact_validator import validate_against_schema
    source, native, _ = factory_return_native[name]
    context = {}
    atlas._normalize_polyglot_symbols(native, source, "typescript", file_evidence_context=context)
    if name in {"block", "expression", "unicode", "record_budget","function","function_unicode","function_record_budget"}:
        def forbid(*a, **k): raise AssertionError("Do not rebuild the file coordinate map")
        monkeypatch.setattr(atlas, "_utf16_offsets_to_utf8_byte_offsets", forbid)
        monkeypatch.setattr(atlas, "_native_utf16_span_validator", forbid)
    result = atlas._factory_return_construction_evidence(native, source, context)
    schema = json.loads((Path(__file__).resolve().parents[2] / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    path = tmp_path / "factory-schema.json"
    path.write_text(json.dumps({"$ref": "#/$defs/factory_return_construction_evidence", "$defs": schema["$defs"]}), encoding="utf-8")
    assert validate_against_schema(path, "factory", result) == []
    if result["records"]:
        from copy import deepcopy
        for key,value in (("type_only", 0), ("source", "x" * 181)):
            invalid = deepcopy(result)
            invalid["records"][0]["import_binding"][key] = value
            assert validate_against_schema(path, "factory", invalid)
        for key,value in (("factory_kind","unknown"),("factory_kind",True)):
            invalid=deepcopy(result)
            invalid["records"][0][key]=value
            assert validate_against_schema(path,"factory",invalid)
        if name.startswith("function"):
            invalid=deepcopy(result)
            invalid["records"][0]["body_kind"]="expression"
            assert validate_against_schema(path,"factory",invalid)
    for key,value in (("factory_semantics", "proved"), ("schema_authority", "validated"), ("mutation_proposed", 0), ("extra", True)):
        assert validate_against_schema(path, "factory", {**result, key:value})


def test_factory_return_real_atlas_sqlite_transport(tmp_path):
    from tools.core.atlas_io import load_atlas_file_metadata
    from tools.core.external_target_generation import external_target_output_slug
    from tools.core.source_snapshot_reader import read_source_snapshot_binding
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / "factory-source"
    target.mkdir()
    source = "\ufeff// 😀\r\n" + _factory_argument_source().replace("\n", "\r\n") + "\r\nthrow new Error('MUST_NOT_EXECUTE');"
    (target / "Factory.tsx").write_bytes(source.encode("utf-8"))
    env = dict(os.environ)
    for key in ("CODEMAPS_TARGET_PREFLIGHT_RECEIPT", "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256", "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE", "CODEMAPS_TARGET_PROJECTS"):
        env.pop(key, None)
    env["CODEMAPS_TARGET_ROOT"] = str(target)
    process = subprocess.run([sys.executable, "-B", "-m", "tools.engines.generate_atlas"], cwd=root, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert process.returncode == 0, process.stdout[-1600:] + process.stderr[-1600:]
    raw = root / "output/external_targets" / external_target_output_slug(str(target)) / ".raw"
    info = load_atlas_file_metadata(raw, "MAIN", "Factory.tsx")
    lane = info["factory_return_construction_evidence"]
    assert lane["status"] == "observed" and lane["record_count"] == 1
    assert lane["records"][0]["methods"] == ["string", "regex"]
    snapshot = read_source_snapshot_binding(raw, "MAIN", "Factory.tsx", expected_hash=info["hash"])
    assert snapshot["status"] == "ok" and snapshot["content"].encode("utf-8") == source.encode("utf-8")
    assert lane["schema_authority"] == lane["factory_semantics"] == "not_established" and lane["runtime_proof"] == "not_performed"
    arguments = info["factory_argument_const_evidence"]
    assert arguments["status"] == "observed" and arguments["record_count"] == 2
    assert [(row["name"],row["binding_scope"]) for row in arguments["records"]] == [("PATTERN","module_const"),("message","factory_const")]
    assert arguments["argument_semantics"] == "not_established" and arguments["mutation_proposed"] is False


def _factory_argument_source():
    return _factory_return_source().replace("export const", "const PATTERN = /@/;\nexport const")


def _resolver_factory_cases():
    source = """import {zodResolver as resolverAdapter} from '@hookform/resolvers/zod';
import {makeSchema as resolverFactory} from './never_execute';
const secretRotation = opaque;
export const Form = () => ({resolver: resolverAdapter(resolverFactory(Boolean(secretRotation)))});
"""
    cases = {"positive":(source,1),"unicode":("\ufeff// e\u0301 \U0001f680\r\n"+source.replace("\n","\r\n"),1),
        "zero":(source.replace("Boolean(secretRotation)",""),1),"opaque_four":(source.replace("Boolean(secretRotation)","first(), {value}, 3, (other)"),1),
        "empty":("const value = 1;",0)}
    replacements = {
        "wrong_module":("@hookform/resolvers/zod","unrelated"), "wrong_export":("zodResolver as resolverAdapter","other as resolverAdapter"),
        "resolver_type":("import {zodResolver","import type {zodResolver"), "factory_type":("import {makeSchema","import type {makeSchema"),
        "resolver_default":("{zodResolver as resolverAdapter}","resolverAdapter"), "factory_default":("{makeSchema as resolverFactory}","resolverFactory"),
        "resolver_optional":("resolverAdapter(resolverFactory","resolverAdapter?.(resolverFactory"),
        "factory_optional":("resolverFactory(Boolean","resolverFactory?.(Boolean"),
        "resolver_generic":("resolverAdapter(resolverFactory","resolverAdapter<Schema>(resolverFactory"),
        "factory_generic":("resolverFactory(Boolean","resolverFactory<Schema>(Boolean"),
        "factory_member":("resolverAdapter(resolverFactory(","resolverAdapter(object.resolverFactory("),
        "factory_wrapped":("resolverAdapter(resolverFactory(Boolean(secretRotation)))","resolverAdapter((resolverFactory(Boolean(secretRotation))))"),
        "factory_cast":("resolverAdapter(resolverFactory(Boolean(secretRotation)))","resolverAdapter(resolverFactory(Boolean(secretRotation)) as Schema)"),
        "resolver_extra":("resolverAdapter(resolverFactory(Boolean(secretRotation)))","resolverAdapter(resolverFactory(Boolean(secretRotation)), options)"),
        "resolver_trailing":("resolverAdapter(resolverFactory(Boolean(secretRotation)))","resolverAdapter(resolverFactory(Boolean(secretRotation)),)"),
        "factory_trailing":("Boolean(secretRotation)","Boolean(secretRotation),"),
        "factory_spread":("Boolean(secretRotation)","...values"), "factory_five":("Boolean(secretRotation)","a,b,c,d,e"),
        "not_factory_call":("resolverFactory(Boolean(secretRotation))","resolverFactory"),
        "namespace_factory":("resolverAdapter(resolverFactory(","resolverAdapter(Factories.resolverFactory("),
        "resolver_shadow":("() => ({resolver:","(resolverAdapter) => ({resolver:"),
        "factory_shadow":("() => ({resolver:","(resolverFactory) => ({resolver:"),
    }
    for name,(before,after) in replacements.items():
        assert before in source,name
        cases[name]=(source.replace(before,after),0)
    for name,write in (("resolver_write","resolverAdapter = other;"),("factory_write","resolverFactory.prop = other;")):
        cases[name]=(source+write,0)
    cases["diagnostics"]=(source+"const = ;",0)
    cases["byte_budget"]=(source+"/*"+"x"*1048576+"*/",0)
    cases["node_budget"]=(source+";0"*11000,0)
    prefix=source[:source.index("export const Form")]
    cases["record_budget"]=(prefix+"\n".join(f"const slot{i} = resolverAdapter(resolverFactory(i{i}));" for i in range(65)),64)
    return cases


@pytest.fixture(scope="module")
def resolver_factory_native(tmp_path_factory):
    root=Path(__file__).resolve().parents[2]
    directory=tmp_path_factory.mktemp("resolver-factory-native")
    cases=_resolver_factory_cases()
    paths=[]
    for name,(source,_) in cases.items():
        path=directory/(name+".tsx"); path.write_bytes(source.encode("utf-8")); paths.append(str(path))
    result=subprocess.run(["node",str(root/"tools/engines/ast_sequencer.cjs"),"--batch-json",*paths],cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=90)
    assert result.returncode==0,result.stderr
    payload=json.loads(result.stdout)
    return {name:(source,payload[path],count) for (name,(source,count)),path in zip(cases.items(),paths)}


@pytest.mark.parametrize("name",list(_resolver_factory_cases()))
def test_resolver_factory_native_and_transport(resolver_factory_native,name,tmp_path):
    from tools.engines import generate_atlas as atlas
    from tools.core.artifact_validator import validate_against_schema
    source,native,expected=resolver_factory_native[name]
    lane=next(r for r in native if r["name"]=="__file_meta__")["resolverFactoryEvidence"]
    assert lane["record_count"]==expected,(name,lane)
    result=atlas._resolver_factory_evidence(native,source)
    assert result["record_count"]==expected,(name,result)
    assert result["factory_semantics"]==result["argument_semantics"]==result["schema_authority"]=="not_established"
    assert result["runtime_proof"]=="not_performed" and result["mutation_proposed"] is False
    if name in {"diagnostics","node_budget","byte_budget"}: assert result["status"]=="unavailable"
    elif name=="record_budget": assert result["status"]=="partial" and result["omitted"]==1
    else: assert result["status"]=="observed"
    schema=json.loads((Path(__file__).resolve().parents[2]/"config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    path=tmp_path/"resolver-schema.json"
    path.write_text(json.dumps({"$ref":"#/$defs/resolver_factory_evidence","$defs":schema["$defs"]}),encoding="utf-8")
    assert validate_against_schema(path,"resolver",result)==[]
    assert validate_against_schema(path,"resolver",{**result,"mutation_proposed":0})
    if expected:
        row=result["records"][0]; encoded=source.encode("utf-8")
        for role,text in (("resolver_ref",b"resolverAdapter"),("factory_ref",b"resolverFactory")):
            span=row["spans"][role]; assert encoded[span["start"]:span["end"]]==text


@pytest.mark.parametrize("damage",["missing","count_bool","count","partial","hash","offset","version","extra","row_extra","argument_count","name","binding","module","parent","duplicate","hidden"])
def test_resolver_factory_transport_refuses_corruption(resolver_factory_native,damage):
    from copy import deepcopy
    from tools.engines import generate_atlas as atlas
    source,native,_=resolver_factory_native["record_budget" if damage=="hidden" else "positive"]
    native=deepcopy(native); meta=next(r for r in native if r["name"]=="__file_meta__"); lane=meta["resolverFactoryEvidence"]
    row=lane["records"][0]
    if damage=="missing": meta.pop("resolverFactoryEvidence")
    elif damage=="count_bool": lane["record_count"]=True
    elif damage=="count": lane["record_count"]+=1
    elif damage=="partial": lane.update(status="partial",omitted=1,reason="record_budget_exceeded")
    elif damage=="hash": lane["source_hash"]="0"*64
    elif damage=="offset": lane["offset_unit"]="utf8_bytes"
    elif damage=="version": lane["version"]="old"
    elif damage=="extra": lane["runtime_proof"]="performed"
    elif damage=="row_extra": row["value"]="inferred"
    elif damage=="argument_count": row["argument_count"]=True
    elif damage=="name": row["resolver_local_name"]="other"
    elif damage=="binding": row["spans"]["factory_import_binding"]["end"]-=1
    elif damage=="module": row["spans"]["resolver_module"]["start"]+=1
    elif damage=="parent": row["spans"]["factory_call"]=deepcopy(row["spans"]["resolver_call"])
    elif damage=="duplicate": lane["records"].append(deepcopy(row)); lane["record_count"]+=1
    elif damage=="hidden": lane["records"][-1]["factory_local_name"]="other"
    result=atlas._resolver_factory_evidence(native,source)
    assert result["status"]=="unavailable" and not result["records"] and result["record_count"]==0


def _callback_guard_sources():
    source="import {z} from 'never-execute';\nexport const make = (isUpdate: boolean) => z.min(isUpdate).superRefine((val,ctx) => { if(isUpdate) return; ctx.addIssue(val); });\nthrow new Error('Never execute');"
    cases={"arrow":(source,1),"unicode":("\ufeff// 😀\r\n"+source.replace("\n","\r\n"),1),
        "function":(source.replace("export const make = (isUpdate: boolean) =>", "export function make(isUpdate: boolean) { return").replace(";\nthrow", "; }\nthrow"),1),
        "comments":(source.replace("if(isUpdate) return;","if(/*guard*/isUpdate) /*then*/ return;"),1),
        "zero_params":(source.replace("(val,ctx)","()"),1)}
    changes={"async":("(val,ctx) =>", "async (val,ctx) =>"), "function_callback":("(val,ctx) =>", "function(val,ctx)"),
        "negated":("if(isUpdate)","if(!isUpdate)"), "property":("if(isUpdate)","if(isUpdate.value)"),
        "wrapped":("if(isUpdate)","if((isUpdate))"),"else":("return; ctx", "return; else throw val; ctx"),
        "return_value":("return; ctx","return val; ctx"),"block_return":("return; ctx","{ return; } ctx"),
        "later_guard":("{ if(isUpdate)","{ unknown(); if(isUpdate)"),"nested_guard":("{ if(isUpdate) return;", "{ if(val) { if(isUpdate) return; }"),
        "wrong_method":(".superRefine(",".refine("),"wrapped_callback":(".superRefine((val,ctx) => { if(isUpdate) return; ctx.addIssue(val); })", ".superRefine(((val,ctx) => { if(isUpdate) return; ctx.addIssue(val); }))"),
        "shadow_callback":("(val,ctx)","(isUpdate,ctx)"),"typed_callback":("(val,ctx)","(val: unknown,ctx)"),
        "default_callback":("(val,ctx)","(val=other,ctx)"),"rest_callback":("(val,ctx)","(...val)"),
        "trailing_callback":("(val,ctx)","(val,ctx,)"),"duplicate_callback":("(val,ctx)","(val,val)"),
        "trailing_argument":("ctx.addIssue(val); });", "ctx.addIssue(val); },);"),
        "spread_argument":("ctx.addIssue(val); });", "ctx.addIssue(val); },...other);"),
        "escaped":("if(isUpdate)","if(isUpda\\u0074e)"),"factory_optional":("isUpdate: boolean", "isUpdate?: boolean")}
    cases.update({name:(source.replace(a,b),0) for name,(a,b) in changes.items()})
    cases.update(written=(source+"isUpdate = true;",1),
        callback_write=(source.replace("ctx.addIssue(val);", "isUpdate = false;"),0),
        late_shadow=(source+"function other(isUpdate: unknown) {}",0),
        byte_budget=(source+"/*"+"x"*1048576+"*/",0),
        node_budget=(source+"const huge=["+",".join("0" for _ in range(11000))+"];",0),
        budget=("import {z} from 'never-execute';\n"+"\n".join(f"export const make{i} = (p{i}) => z."+".".join(f"superRefine(() => {{ if(p{i}) return; }})" for _ in range(8))+";" for i in range(9)),64))
    return cases


@pytest.fixture(scope="module")
def callback_guard_native(tmp_path_factory):
    directory=tmp_path_factory.mktemp("callback-guard-source"); cases=_callback_guard_sources(); paths=[]
    for name,(source,_) in cases.items():
        path=directory/(name+".tsx"); path.write_bytes(source.encode("utf-8")); paths.append(str(path))
    process=subprocess.run(["node",str(Path(__file__).resolve().parents[2]/"tools/engines/ast_sequencer.cjs"),"--batch-json",*paths],capture_output=True,text=True,encoding="utf-8",timeout=90)
    assert process.returncode==0,process.stderr
    rows=json.loads(process.stdout)
    return {name:(source,rows[path],expected) for (name,(source,expected)),path in zip(cases.items(),paths)}


@pytest.mark.parametrize("name",list(_callback_guard_sources()))
def test_callback_guard_native_source_and_shared_transport(callback_guard_native,name,tmp_path,monkeypatch):
    from tools.engines import generate_atlas as atlas
    from tools.core.artifact_validator import validate_against_schema
    from tools.core.factory_return_contract import factory_return_stored_problem
    from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION
    from tools.inspect_target import _safeparse_snapshot_spans
    source,native,expected=callback_guard_native[name]; context={}
    atlas._normalize_polyglot_symbols(native,source,"typescript",file_evidence_context=context)
    def forbid(*a,**k): raise AssertionError("No second coordinate index")
    if name not in {"byte_budget","node_budget"}:
        monkeypatch.setattr(atlas,"_utf16_offsets_to_utf8_byte_offsets",forbid)
        monkeypatch.setattr(atlas,"_native_utf16_span_validator",forbid)
    lane=atlas._factory_return_construction_evidence(native,source,context)
    assert sum(len(r.get("callback_guard_reads",[])) for r in lane["records"])==expected,(name,lane)
    schema=json.loads((Path(__file__).resolve().parents[2]/"config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    path=tmp_path/"schema.json"; path.write_text(json.dumps({"$ref":"#/$defs/factory_return_construction_evidence","$defs":schema["$defs"]}),encoding="utf-8")
    assert validate_against_schema(path,"callback",lane)==[]
    if lane["status"]=="observed":
        info={"factory_return_construction_evidence":lane}; valid=_safeparse_snapshot_spans(info,source,lane["source_hash"],None)
        assert factory_return_stored_problem(lane,lane["source_hash"],valid,ast_contract_version=AST_CONTRACT_VERSION)==""
    if name in {"arrow","unicode","function","comments","zero_params"}:
        row=lane["records"][0]
        assert len(row["parameter_uses"])==1 and row["callback_guard_reads"][0]["call_index"]==1
    if name=="budget": assert sum(r["callback_guard_reads_omitted"] for r in lane["records"])==8
    assert lane["runtime_proof"]=="not_performed" and lane["factory_semantics"]=="not_established"


@pytest.mark.parametrize("damage",["version","missing","extra","bool_call","bool_argument","wrong_method","reference","body","branch","return","callback","argument","parameters","duplicate","omitted","bool_omitted","hidden"])
def test_callback_guard_transport_refuses_all_retained_rows(callback_guard_native,damage):
    from copy import deepcopy
    from tools.engines import generate_atlas as atlas
    source,original,_=callback_guard_native["budget" if damage=="hidden" else "arrow"]
    raw=deepcopy(original); lane=next(r for r in raw if r["name"]=="__file_meta__")["factoryReturnConstructionEvidence"]
    row=lane["records"][-1 if damage=="hidden" else 0]; guard=row["callback_guard_reads"][0] if row["callback_guard_reads"] else None
    if damage=="version": lane["version"]="old"
    elif damage=="missing": row.pop("callback_guard_reads")
    elif damage=="extra": guard["executed"]=True
    elif damage=="bool_call": guard["call_index"]=True
    elif damage=="bool_argument": guard["argument_index"]=False
    elif damage=="wrong_method": row["methods"][guard["call_index"]]="refine"
    elif damage in {"reference","body","branch","return","callback"}: row["spans"]["guard_0_"+damage]["start"]+=1
    elif damage=="argument": row["spans"]["guard_0_argument_0"]=deepcopy(row["spans"]["parameter"])
    elif damage=="parameters": guard["callback_parameters"]=["isUpdate"]
    elif damage=="duplicate": row["callback_guard_reads"].append(deepcopy(guard))
    elif damage in {"omitted","hidden"}: row["callback_guard_reads_omitted"]=-1
    elif damage=="bool_omitted": row["callback_guard_reads_omitted"]=True
    result=atlas._factory_return_construction_evidence(raw,source)
    assert result["status"]=="unavailable" and not result["records"],(damage,result)


def _factory_argument_cases():
    source = _factory_argument_source()
    cases = {"positive":(source,2), "unicode":("\ufeff// 😀\r\n" + source.replace("\n","\r\n"),2),
        "expression":("import {z} from 'zod'; const PATTERN = /@/; export const make = () => z.regex(PATTERN);",1),
        "empty":("export const ordinary = () => 1;",0), "diagnostics":("import {",0),
        "opaque_initializer":(source.replace("PATTERN = /@/", "PATTERN = unknownSideEffect()"),2),
        "alias":(source.replace("{ z }", "{ z as validator }").replace("z.string()", "validator.string()"),2),
        "unknown_library":(source.replace("'zod'", "'uninstalled-unknown-library'"),2)}
    changes = {"direct":("{ message }","message",2), "mutable":("const PATTERN","let PATTERN",1),
        "typed":("PATTERN =", "PATTERN: unknown =",1), "unknown_reference":("regex(PATTERN", "regex(MISSING",1),
        "parameter":("regex(PATTERN", "regex(options",1), "wrapped":("regex(PATTERN", "regex((PATTERN)",1),
        "literal_not_reference":("regex(PATTERN", "regex('PATTERN'",1),
        "property":("{ message }","{ message: message }",1), "spread_object":("{ message }","{ ...message }",1),
        "duplicate_property":("{ message }","{ message, message }",1), "object_trailing_comma":("{ message }","{ message, }",1),
        "trailing_comma":("{ message });","{ message },);",0), "spread_argument":("regex(PATTERN", "regex(...PATTERN",0),
        "argument_budget":("{ message });","{ message }, 1, 2, 3);",0),
        "destructured_binding":("const PATTERN = /@/;","const {PATTERN} = object;",1),
        "local_shorthand_write":("return z.string()", "const ignored = (({message} = object)); return z.string()",1)}
    cases.update({name:(source.replace(a,b),count) for name,(a,b,count) in changes.items()})
    cases.update(late=(source.replace("const PATTERN = /@/;\n","") + "const PATTERN = /@/;",1),
        assigned=(source + "PATTERN = other;",1), compound=(source + "PATTERN += other;",1),
        property_write=(source + "PATTERN.extra = other;",1), destructuring_write=(source + "({PATTERN} = other);",1),
        shadow=(source + "function other(PATTERN: unknown) {}",1),
        record_budget=("import {z} from 'zod'; const PATTERN = /@/;\n" + "\n".join(f"export const make{i} = () => z.regex(PATTERN, PATTERN);" for i in range(33)),64),
        parent_budget=("import {z} from 'zod';\n" + "\n".join(f"export const make{i} = () => z.string();" for i in range(65)),0),
        node_budget=(source + "const huge = [" + ",".join("0" for _ in range(11000)) + "];",0),
        byte_budget=(source + "/*" + "x" * 1048576 + "*/",0))
    function=_factory_function_source(source)
    cases.update(function=(function,2),function_unicode=("\ufeff// 😀\r\n"+function.replace("\n","\r\n"),2),
        function_opaque=(function.replace("PATTERN = /@/","PATTERN = mustNeverExecute()"),2),
        function_mutable=(function.replace("const PATTERN","let PATTERN"),1),
        function_written=(function+"PATTERN = other;",1),
        function_late=(function.replace("const PATTERN = /@/;\n","")+"const PATTERN = /@/;",1),
        function_parameter=(function.replace("regex(PATTERN","regex(options"),1),
        function_shadow=(function+"function other(message: string) {}",1))
    return cases


@pytest.fixture(scope="module")
def factory_argument_native(tmp_path_factory):
    root = Path(__file__).resolve().parents[2]
    cases = _factory_argument_cases()
    directory = tmp_path_factory.mktemp("factory-arguments")
    paths = []
    for name,(source,_) in cases.items():
        path = directory / (name + ".tsx")
        path.write_bytes(source.encode("utf-8"))
        paths.append(str(path))
    process = subprocess.run(["node",str(root / "tools/engines/ast_sequencer.cjs"),"--batch-json",*paths],cwd=root,
        capture_output=True,text=True,encoding="utf-8",timeout=90)
    assert process.returncode == 0, process.stderr
    rows = json.loads(process.stdout)
    return {name:(source,rows[path],count) for (name,(source,count)),path in zip(cases.items(),paths)}


@pytest.mark.parametrize("name",list(_factory_argument_cases()))
def test_factory_argument_source_const_links_and_unknown_values(factory_argument_native,name):
    from tools.engines import generate_atlas as atlas
    source,native,expected = factory_argument_native[name]
    meta = next(row for row in native if row["name"] == "__file_meta__")
    lane = meta["factoryArgumentConstEvidence"]
    assert lane["record_count"] == expected, (name,lane)
    result = atlas._factory_argument_const_evidence(native,source)
    assert result["record_count"] == expected, (name,result)
    assert result["status"] == ("unavailable" if name in {"parent_budget","node_budget","byte_budget","diagnostics"} else "partial" if name == "record_budget" else "observed")
    assert result["schema_authority"] == result["factory_semantics"] == result["argument_semantics"] == "not_established"
    assert result["runtime_proof"] == "not_performed" and result["mutation_proposed"] is False
    if name in {"positive","unicode","opaque_initializer","alias","unknown_library"}:
        assert [(row["name"],row["binding_scope"],row["argument_index"]) for row in result["records"]] == [("PATTERN","module_const",0),("message","factory_const",1)]
    if name == "record_budget": assert result["omitted"] == 2
    if name == "parent_budget": assert result["reason"] == "construction_record_budget_exceeded"


@pytest.mark.parametrize("damage",["missing","hash","version","parser","count","bool_count","omitted","extra","name","kind","scope","reference","initializer","const","line","index","index_bool","argument_count","argument_count_bool","argument_index","order","duplicate","parent_missing","parent_hash","parent_method","parent_partial","hidden","function_kind","function_parent_kind","missing_factory_kind","function_module_order"])
def test_factory_argument_transport_refuses_corrupt_or_unjoined(factory_argument_native,damage):
    from copy import deepcopy
    from tools.engines import generate_atlas as atlas
    source,original,_ = factory_argument_native["record_budget" if damage == "hidden" else "function" if damage.startswith("function_") else "positive"]
    raw = deepcopy(original)
    meta = next(row for row in raw if row["name"] == "__file_meta__")
    lane = meta["factoryArgumentConstEvidence"]
    row = lane["records"][-1 if damage == "hidden" else 0]
    if damage == "missing": meta.pop("factoryArgumentConstEvidence")
    elif damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "version": lane["version"] = "old"
    elif damage == "parser": meta["parserStatus"] = "degraded"
    elif damage == "count": lane["record_count"] += 1
    elif damage == "bool_count": lane["record_count"] = True
    elif damage == "omitted": lane["omitted"] = 1
    elif damage == "extra": row["value"] = "validated"
    elif damage == "name": row["name"] = "other"
    elif damage == "kind": row["kind"] = "object_shorthand"
    elif damage == "scope": row["binding_scope"] = "factory_const"
    elif damage == "reference": row["spans"]["reference"] = row["spans"]["binding"]
    elif damage == "initializer": row["spans"]["initializer"] = row["spans"]["binding"]
    elif damage == "const": row["spans"]["const_keyword"] = row["spans"]["binding"]
    elif damage in {"line","hidden"}: row["spans"]["reference"]["line"] += 1
    elif damage == "index": row["call_index"] = 0
    elif damage == "index_bool": row["call_index"] = True
    elif damage == "argument_count": row["argument_count"] = 1
    elif damage == "argument_count_bool": row["argument_count"] = True
    elif damage == "argument_index": row["argument_index"] = 1
    elif damage == "order": row["spans"]["argument_0"],row["spans"]["argument_1"] = row["spans"]["argument_1"],row["spans"]["argument_0"]
    elif damage == "duplicate": lane["records"].append(deepcopy(row)); lane["record_count"] += 1
    elif damage == "parent_missing": meta.pop("factoryReturnConstructionEvidence")
    elif damage == "parent_hash": meta["factoryReturnConstructionEvidence"]["source_hash"] = "0" * 64
    elif damage == "parent_method": meta["factoryReturnConstructionEvidence"]["records"][0]["methods"][0] = "other"
    elif damage == "parent_partial": meta["factoryReturnConstructionEvidence"].update(status="partial",reason="record_budget_exceeded",omitted=1)
    elif damage == "function_kind": row["factory_kind"]="const_arrow"
    elif damage == "function_parent_kind": meta["factoryReturnConstructionEvidence"]["records"][0]["factory_kind"]="const_arrow"
    elif damage == "missing_factory_kind": row.pop("factory_kind")
    elif damage == "function_module_order": row["spans"]["declaration_list"]=row["spans"]["factory"]
    result = atlas._factory_argument_const_evidence(raw,source)
    assert result["status"] == "unavailable" and result["records"] == [] and result["reason"], (damage,result)


@pytest.mark.parametrize("name",["positive","unicode","empty","record_budget","parent_budget","node_budget","function","function_unicode"])
def test_factory_argument_schema_and_shared_coordinate_batch(factory_argument_native,tmp_path,monkeypatch,name):
    from copy import deepcopy
    from tools.engines import generate_atlas as atlas
    from tools.core.artifact_validator import validate_against_schema
    source,native,_ = factory_argument_native[name]
    context = {}
    atlas._normalize_polyglot_symbols(native,source,"typescript",file_evidence_context=context)
    if name in {"positive","unicode","record_budget","function","function_unicode"}:
        def forbid(*a,**k): raise AssertionError("No second coordinate/hash index in the production path")
        monkeypatch.setattr(atlas,"_utf16_offsets_to_utf8_byte_offsets",forbid)
        monkeypatch.setattr(atlas,"_native_utf16_span_validator",forbid)
    result = atlas._factory_argument_const_evidence(native,source,context)
    schema = json.loads((Path(__file__).resolve().parents[2] / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    path = tmp_path / "factory-argument-schema.json"
    path.write_text(json.dumps({"$ref":"#/$defs/factory_argument_const_evidence","$defs":schema["$defs"]}),encoding="utf-8")
    assert validate_against_schema(path,"arguments",result) == []
    for key,value in (("argument_semantics","proved"),("runtime_proof","performed"),("mutation_proposed",0),("extra",True)):
        assert validate_against_schema(path,"arguments",{**result,key:value})
    if result["records"]:
        for key,value in (("argument_count",True),("argument_count",5),("kind","other"),("binding_scope","parameter"),("name","x" * 129),("factory_kind","unknown")):
            invalid = deepcopy(result)
            invalid["records"][0][key] = value
            assert validate_against_schema(path,"arguments",invalid), (key,value)
