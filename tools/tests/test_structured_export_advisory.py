from __future__ import annotations

from copy import deepcopy
import json
import hashlib
import os
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path
from xml.etree import ElementTree

import pytest

from tools.core.artifact_validator import ensure_against_schema, validate_against_schema
from tools.core.external_target_generation import external_target_output_slug
from tools.core.xml_format_oracle import MAX_XML_CHARACTERS, evaluate_xml_output
from tools.engines.generate_atlas import _structured_output_evidence
from tools.engines.react_frontier_intelligence import _refactor_plan, analyze_frontier_file


ROOT = Path(__file__).resolve().parents[2]
SEQUENCER = ROOT / "tools/engines/ast_sequencer.cjs"


def _sequence(tmp_path: Path, source: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    target = tmp_path / "export.ts"
    target.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [node, str(SEQUENCER), str(target)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    rows = json.loads(result.stdout)
    return next(row for row in rows if row.get("name") == "__file_meta__")


@pytest.mark.parametrize(
    ("source", "sink", "context"),
    [
        (
            'new Response(`<Root><Title>${title}</Title></Root>`, '
            '{headers: {"Content-Type": "application/xml; charset=utf-8"}})',
            "xml_http_response",
            "element_text",
        ),
        (
            'new Response(`<Item code="${code}"/>`, '
            '{headers: {"Content-Type": "text/xml"}})',
            "xml_http_response",
            "attribute_value",
        ),
        (
            'import * as fs from "node:fs"; '
            'fs.writeFileSync("feed.xml", `<Root><![CDATA[${body}]]></Root>`)',
            "xml_file_write",
            "cdata",
        ),
    ],
)
def test_direct_xml_sinks_emit_bounded_ast_advisory(tmp_path, source, sink, context):
    meta = _sequence(tmp_path, source)
    assert meta["parserStatus"] == "observed"
    evidence = _structured_output_evidence([meta])
    assert evidence["status"] == "observed"
    assert evidence["omitted"] == 0
    assert len(evidence["candidates"]) == 1
    candidate = evidence["candidates"][0]
    assert candidate["sink"] == sink
    assert candidate["interpolation_contexts"] == [context]
    assert candidate["proof_status"] == "needs_format_native_round_trip"
    assert "title" not in json.dumps(candidate)
    finding = analyze_frontier_file(
        "MAIN", "src/export.ts", source,
        {"structured_output_evidence": evidence,
         "hash": hashlib.sha256(source.encode("utf-8")).hexdigest()},
    )["findings"][0]
    assert finding["dimension"] == "structured_output_integrity"
    assert finding["confidence"] == "needs_runtime_proof"
    assert finding["runtime_proof_status"] == "needs_runtime_proof"
    assert finding["risk_tier"] == "low"
    assert "parser round-trip" in finding["recommended_action"]
    assert _refactor_plan([finding]) == {"tasks": [], "total_tasks": 0}
    assert _refactor_plan([
        {**finding, "evidence_scope": "format_native_confirmed"}
    ])["total_tasks"] == 1
    assert analyze_frontier_file(
        "MAIN", "src/export.ts", "const current = true;",
        {"structured_output_evidence": evidence,
         "hash": hashlib.sha256(source.encode("utf-8")).hexdigest()},
    ) is None


@pytest.mark.parametrize(
    ("source", "sink", "context"),
    [
        (
            'const xml = `<Root>${value}</Root>`; '
            'new Response(xml, {headers: {"Content-Type": "application/xml"}})',
            "xml_http_response", "element_text",
        ),
        (
            'function send(value: string) { const xml = `<Item id="${value}"/>`; '
            'return new Response(xml, {headers: {"Content-Type": "text/xml"}}); }',
            "xml_http_response", "attribute_value",
        ),
        (
            'import * as fs from "node:fs"; '
            'const xml = `<Root><![CDATA[${value}]]></Root>`; '
            'fs.writeFileSync("feed.xml", xml)',
            "xml_file_write", "cdata",
        ),
    ],
)
def test_same_file_const_template_reaches_xml_advisory_only(tmp_path, source, sink, context):
    evidence = _structured_output_evidence([_sequence(tmp_path, source)])
    assert evidence["status"] == "observed"
    assert evidence["scope"] == "xml_template_or_imported_mime_helper_candidate"
    assert len(evidence["candidates"]) == 1
    candidate = evidence["candidates"][0]
    assert candidate["sink"] == sink
    assert candidate["interpolation_contexts"] == [context]
    assert candidate["evidence_scope"] == "same_file_const_template_to_literal_xml_sink"
    finding = analyze_frontier_file(
        "MAIN", "src/export.ts", source,
        {"structured_output_evidence": evidence,
         "hash": hashlib.sha256(source.encode("utf-8")).hexdigest()},
    )["findings"][0]
    assert finding["evidence_scope"] == "same_file_const_xml_template_sink_advisory_only"
    assert "same-file const" in finding["evidence"]
    assert finding["confidence"] == "needs_runtime_proof"
    assert _refactor_plan([finding]) == {"tasks": [], "total_tasks": 0}


@pytest.mark.parametrize(
    ("source", "scope"),
    [
        (
            'import { downloadFile as deliver } from "./exportUtils"; '
            'const xml = `<Root><Title>${title}</Title></Root>`; '
            'deliver(xml, "catalog", "xml", "application/xml")',
            "same_file_const_template_to_xml_mime_helper_call",
        ),
        (
            'import deliver from "./exportUtils"; '
            'deliver(`<Item id="${item}"/>`, "catalog", ".xml", "text/xml; charset=utf-8")',
            "direct_template_to_xml_mime_helper_call",
        ),
    ],
)
def test_imported_xml_mime_helper_is_source_bound_advisory_not_proven_sink(
    tmp_path, source, scope,
):
    evidence = _structured_output_evidence([_sequence(tmp_path, source)])
    assert evidence["status"] == "observed"
    assert len(evidence["candidates"]) == 1
    candidate = evidence["candidates"][0]
    assert candidate["sink"] == "xml_mime_helper_call_unverified"
    assert candidate["evidence_scope"] == scope
    assert candidate["proof_status"] == "needs_format_native_round_trip"
    assert "exportUtils" not in json.dumps(candidate)
    finding = analyze_frontier_file(
        "MAIN", "src/export.ts", source,
        {"structured_output_evidence": evidence,
         "hash": hashlib.sha256(source.encode("utf-8")).hexdigest()},
    )["findings"][0]
    assert finding["evidence_scope"] == "xml_mime_helper_call_advisory_only"
    assert finding["confidence"] == "needs_runtime_proof"
    assert "unverified" in finding["evidence"]
    assert _refactor_plan([finding])["total_tasks"] == 0
    assert analyze_frontier_file(
        "MAIN", "src/export.ts", "const changed = true;",
        {"structured_output_evidence": evidence,
         "hash": hashlib.sha256(source.encode("utf-8")).hexdigest()},
    ) is None


@pytest.mark.parametrize("source", [
    'import { deliver } from "./helper"; '
    'deliver("catalog", `${name}_onix`, "xml", '
    '`<Root><Title>${title}</Title></Root>`, "application/xml")',
    'import { deliver } from "./helper"; '
    'const xml = `<Root><Title>${title}</Title></Root>`; '
    'deliver("catalog", `${name}_onix`, "xml", xml, "application/xml", null)',
])
def test_imported_xml_mime_helper_finds_unique_later_payload_argument(tmp_path, source):
    evidence = _structured_output_evidence([_sequence(tmp_path, source)])
    assert evidence["status"] == "observed"
    assert len(evidence["candidates"]) == 1
    candidate = evidence["candidates"][0]
    assert candidate["sink"] == "xml_mime_helper_call_unverified"
    assert candidate["interpolation_contexts"] == ["element_text"]
    assert candidate["proof_status"] == "needs_format_native_round_trip"
    assert analyze_frontier_file(
        "MAIN", "src/export.ts", source,
        {"structured_output_evidence": evidence,
         "hash": hashlib.sha256(source.encode("utf-8")).hexdigest()},
    )["findings"][0]["confidence"] == "needs_runtime_proof"


def test_imported_helper_does_not_mistake_xml_like_filename_for_payload(tmp_path):
    source = (
        'import { deliver } from "./helper"; '
        'deliver("catalog", `<Name>${name}</Name>`, "xml", '
        'serializedXml, "application/xml")'
    )
    evidence = _structured_output_evidence([_sequence(tmp_path, source)])
    assert evidence["status"] == "observed"
    assert evidence["candidates"] == []


def test_imported_helper_with_two_xml_like_arguments_is_ambiguous(tmp_path):
    source = (
        'import { deliver } from "./helper"; '
        'deliver(`<Root>${body}</Root>`, `<Name>${name}</Name>`, '
        '"xml", "application/xml")'
    )
    evidence = _structured_output_evidence([_sequence(tmp_path, source)])
    assert evidence["status"] == "observed"
    assert evidence["candidates"] == []


@pytest.mark.parametrize(
    "source",
    [
        'const xml = `<Root>${value}</Root>`; '
        'deliver(xml, "catalog", "xml", "application/xml")',
        'function deliver(...args: any[]) {} '
        'deliver(`<Root>${value}</Root>`, "catalog", "xml", "application/xml")',
        'import { deliver } from "./exportUtils"; '
        'function save(deliver: Function) { '
        'deliver(`<Root>${value}</Root>`, "catalog", "xml", "application/xml"); }',
        'import type { deliver } from "./exportUtils"; '
        'deliver(`<Root>${value}</Root>`, "catalog", "xml", "application/xml")',
        'import { deliver } from "./exportUtils"; '
        'deliver(`<Root>${value}</Root>`, "catalog", "json", "application/xml")',
        'import { deliver } from "./exportUtils"; '
        'deliver(`<Root>${value}</Root>`, "catalog", "xml", "text/html")',
        'import { deliver } from "./exportUtils"; '
        'const mime = "application/xml"; '
        'deliver(`<Root>${value}</Root>`, "catalog", "xml", mime)',
        'import { deliver } from "./exportUtils"; '
        'const xml = serializeXml(value); '
        'deliver(xml, "catalog", "xml", "application/xml")',
    ],
)
def test_unproven_xml_helper_identity_or_literal_contract_is_not_reported(tmp_path, source):
    evidence = _structured_output_evidence([_sequence(tmp_path, source)])
    assert evidence["status"] == "observed"
    assert evidence["candidates"] == []


@pytest.mark.parametrize(
    "source",
    [
        'class Response { constructor(body: string, options: object) {} } '
        'new Response(`<Root>${value}</Root>`, '
        '{headers: {"Content-Type": "application/xml"}})',
        'function send(Response: any) { return new Response(`<Root>${value}</Root>`, '
        '{headers: {"Content-Type": "application/xml"}}); }',
        'const fs = {writeFileSync(_name: string, _body: string) {}}; '
        'fs.writeFileSync("feed.xml", `<Root>${value}</Root>`)',
        'import * as fs from "node:fs"; '
        'function save(fs: {writeFileSync: Function}) { '
        'fs.writeFileSync("feed.xml", `<Root>${value}</Root>`); }',
        'import * as fs from "fake-fs"; '
        'fs.writeFileSync("feed.xml", `<Root>${value}</Root>`)',
        'function save(require: any) { const fs = require("node:fs"); '
        'fs.writeFileSync("feed.xml", `<Root>${value}</Root>`); }',
        'import {Response} from "custom-response"; '
        'new Response(`<Root>${value}</Root>`, '
        '{headers: {"Content-Type": "application/xml"}})',
    ],
)
def test_shadowed_xml_sink_names_are_not_claimed_as_output_sinks(tmp_path, source):
    evidence = _structured_output_evidence([_sequence(tmp_path, source)])
    assert evidence["status"] == "observed"
    assert evidence["candidates"] == []
    assert evidence["omitted"] == 0


@pytest.mark.parametrize(
    "source",
    [
        'import * as storage from "node:fs"; '
        'storage.writeFileSync("feed.xml", `<Root>${value}</Root>`)',
        'const storage = require("node:fs"); '
        'storage.writeFileSync("feed.xml", `<Root>${value}</Root>`)',
        'import * as storage from "node:fs/promises"; '
        'storage.writeFile("feed.xml", `<Root>${value}</Root>`)',
    ],
)
def test_bound_node_fs_alias_remains_an_xml_file_sink(tmp_path, source):
    evidence = _structured_output_evidence([_sequence(tmp_path, source)])
    assert evidence["status"] == "observed"
    assert len(evidence["candidates"]) == 1
    assert evidence["candidates"][0]["sink"] == "xml_file_write"


@pytest.mark.parametrize(
    "source",
    [
        'return getServerSideSitemap([{loc: "https://example.com/a&b"}])',
        'new Response(`<Root>${value}</Root>`)',
        'let xml = `<Root>${value}</Root>`; '
        'new Response(xml, {headers: {"Content-Type": "application/xml"}})',
        'const xml = `<Root>${value}</Root>`; '
        'function send(xml: string) { return new Response(xml, '
        '{headers: {"Content-Type": "application/xml"}}); }',
        'const xml = `<Root>${value}</Root>`; const copy = xml; '
        'new Response(copy, {headers: {"Content-Type": "application/xml"}})',
        'const xml = serializeXml(value); '
        'new Response(xml, {headers: {"Content-Type": "application/xml"}})',
        'import { xml } from "./builder"; '
        'new Response(xml, {headers: {"Content-Type": "application/xml"}})',
        'new Response(`<Root>${value}</Root>`, {headers: {"Content-Type": "text/html"}})',
    ],
)
def test_builder_and_unproven_sink_do_not_become_xml_findings(tmp_path, source):
    meta = _sequence(tmp_path, source)
    evidence = _structured_output_evidence([meta])
    assert evidence == {
        "status": "observed",
        "scope": "xml_template_or_imported_mime_helper_candidate",
        "candidates": [],
        "omitted": 0,
    }
    assert analyze_frontier_file(
        "MAIN", "src/export.ts", source,
        {"structured_output_evidence": evidence},
    ) is None


def test_legacy_or_malformed_ast_metadata_is_unavailable_not_clean():
    for meta in (
        {"name": "__file_meta__", "parserStatus": "observed"},
        {"name": "__file_meta__", "parserStatus": "degraded",
         "structuredOutputScanStatus": "observed",
         "structuredOutputCandidates": [], "structuredOutputCandidatesOmitted": 0},
        {"name": "__file_meta__", "parserStatus": "observed",
         "structuredOutputScanStatus": "observed",
         "structuredOutputCandidates": [{"kind": "xml_template_interpolation_candidate"}],
         "structuredOutputCandidatesOmitted": 0},
        {"name": "__file_meta__", "parserStatus": "observed",
         "structuredOutputScanStatus": "observed",
         "structuredOutputScanVersion": "v2-xml-mime-helper-candidate",
         "structuredOutputCandidates": [],
         "structuredOutputCandidatesOmitted": 0},
        {"name": "__file_meta__", "parserStatus": "observed",
         "structuredOutputScanStatus": "observed",
         "structuredOutputScanVersion": "v3-xml-mime-helper-argument-candidate",
         "structuredOutputCandidates": [{
             "kind": "xml_template_interpolation_candidate",
             "sink": "xml_mime_helper_call_unverified",
             "interpolation_contexts": ["element_text"],
             "interpolation_count": 1,
             "line": 1,
             "evidence_scope": "direct_template_to_literal_xml_sink",
             "proof_status": "needs_format_native_round_trip",
         }], "structuredOutputCandidatesOmitted": 0},
    ):
        assert _structured_output_evidence([meta])["status"] == "unavailable"


@pytest.mark.parametrize(
    ("raw_xml", "encoded_xml", "expected"),
    [
        ("<Root>A&B</Root>", f"<Root>{escape('A&B')}</Root>", "A&B"),
        ('<Root code="A&B"/>', f'<Root code="{escape("A&B", quote=True)}"/>', "A&B"),
        ("<Root><![CDATA[A]]>B]]></Root>",
         "<Root><![CDATA[A]]]]><![CDATA[>B]]></Root>", "A]]>B"),
    ],
)
def test_format_native_xml_round_trip_calibrates_candidate_not_proof(raw_xml, encoded_xml, expected):
    with pytest.raises(ElementTree.ParseError):
        ElementTree.fromstring(raw_xml)
    root = ElementTree.fromstring(encoded_xml)
    assert (root.attrib.get("code") if root.attrib else root.text) == expected


def test_well_formed_namespaced_xml_injection_requires_structure_and_value_round_trip(tmp_path):
    source = (
        'new Response(`<onix:Product xmlns:onix="urn:onix">'
        '<onix:Title>${title}</onix:Title></onix:Product>`, '
        '{headers: {"Content-Type": "application/xml"}})'
    )
    evidence = _structured_output_evidence([_sequence(tmp_path, source)])
    assert evidence["status"] == "observed"
    assert evidence["candidates"][0]["interpolation_contexts"] == ["element_text"]
    finding = analyze_frontier_file(
        "MAIN", "src/export.ts", source,
        {"structured_output_evidence": evidence,
         "hash": hashlib.sha256(source.encode("utf-8")).hexdigest()},
    )["findings"][0]
    assert finding["confidence"] == "needs_runtime_proof"
    assert "structure and original values" in finding["recommended_action"]
    assert _refactor_plan([finding])["total_tasks"] == 0

    title = "</onix:Title><Injected/><onix:Title>"
    raw = (
        '<onix:Product xmlns:onix="urn:onix"><onix:Title>'
        + title + "</onix:Title></onix:Product>"
    )
    parsed_raw = ElementTree.fromstring(raw)
    assert len(parsed_raw) == 3  # Well-formed XML, but the output tree changed.
    encoded = (
        '<onix:Product xmlns:onix="urn:onix"><onix:Title>'
        + escape(title) + "</onix:Title></onix:Product>"
    )
    parsed_encoded = ElementTree.fromstring(encoded)
    assert len(parsed_encoded) == 1
    assert parsed_encoded[0].tag == "{urn:onix}Title"
    assert parsed_encoded[0].text == title


def test_candidate_budget_is_explicitly_partial(tmp_path):
    source = "\n".join(
        f'new Response(`<Root>${{value{i}}}</Root>`, '
        '{headers: {"Content-Type": "application/xml"}});'
        for i in range(17)
    )
    evidence = _structured_output_evidence([_sequence(tmp_path, source)])
    assert evidence["status"] == "partial_budget"
    assert len(evidence["candidates"]) == 16
    assert evidence["omitted"] == 1
    source_finding = analyze_frontier_file(
        "MAIN", "src/export.ts", source,
        {"structured_output_evidence": evidence,
         "hash": hashlib.sha256(source.encode("utf-8")).hexdigest()},
    )["findings"][0]
    assert "scan partial: 1 candidates omitted" in source_finding["evidence"]


def test_live_source_fallback_preserves_exact_line_endings(tmp_path, monkeypatch):
    from tools.core import source_snapshot_reader as reader

    source = tmp_path / "feed.ts"
    source.write_bytes(b"<Root>value</Root>\r\n")
    monkeypatch.setattr(reader, "ROOT", tmp_path)
    monkeypatch.setattr(reader, "DB_PATH", tmp_path / "missing.db")

    assert reader.load_source_text("MAIN", "feed.ts", fallback_path=source) == "<Root>value</Root>\r\n"


@pytest.mark.parametrize(
    ("source_bytes", "expected_scope"),
    [
        (
            b'const xml = `<Root>${value}</Root>`;\r\n'
            b'new Response(xml, '
            b'{headers: {"Content-Type": "application/xml"}});\r\n',
            "same_file_const_xml_template_sink_advisory_only",
        ),
        (
            b'import { deliver } from "./exportUtils";\r\n'
            b'const xml = `<Root>${value}</Root>`;\r\n'
            b'deliver(xml, "catalog", "xml", "application/xml");\r\n',
            "xml_mime_helper_call_advisory_only",
        ),
    ],
)
def test_real_atlas_projection_reaches_frontier_without_defect_promotion(
    tmp_path, source_bytes, expected_scope,
):
    target = tmp_path / "target"
    target.mkdir()
    source = target / "feed.ts"
    source.write_bytes(source_bytes)
    env = dict(os.environ)
    for key in (
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT",
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256",
        "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE",
        "CODEMAPS_TARGET_PROJECTS",
    ):
        env.pop(key, None)
    env["CODEMAPS_TARGET_ROOT"] = str(target)
    result = subprocess.run(
        [sys.executable, "-m", "tools.engines.generate_atlas"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    atlas_path = (
        ROOT / "output" / "external_targets" / external_target_output_slug(str(target))
        / ".raw" / "atlas.json"
    )
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    ensure_against_schema(ROOT / "config/schemas/atlas.schema.json", "atlas", atlas)
    file_data = atlas["MAIN"]["files"]["feed.ts"]
    assert file_data["structured_output_evidence"]["status"] == "observed"
    if expected_scope == "xml_mime_helper_call_advisory_only":
        mismatched = deepcopy(atlas)
        mismatched["MAIN"]["files"]["feed.ts"]["structured_output_evidence"]["candidates"][0][
            "evidence_scope"
        ] = "direct_template_to_literal_xml_sink"
        assert validate_against_schema(
            ROOT / "config/schemas/atlas.schema.json", "atlas", mismatched,
        )
    with source.open("r", encoding="utf-8", newline="") as source_file:
        content = source_file.read()
    row = analyze_frontier_file("MAIN", "feed.ts", content, file_data)
    assert row["findings"][0]["dimension"] == "structured_output_integrity"
    assert row["findings"][0]["evidence_scope"] == expected_scope
    assert row["findings"][0]["confidence"] == "needs_runtime_proof"
    assert _refactor_plan(row["findings"])["total_tasks"] == 0


def _xml_expectations(title: str) -> dict:
    return {
        "root_tag": "{urn:catalog}Catalog",
        "texts": [
            {"tag": "{urn:catalog}Title", "occurrence": 0, "expected_text": title}
        ],
    }


def test_xml_output_oracle_checks_namespace_and_original_leaf_value():
    title = "A & B < C"
    xml = '<Catalog xmlns="urn:catalog"><Title>A &amp; B &lt; C</Title></Catalog>'
    result = evaluate_xml_output(xml, _xml_expectations(title))
    assert result["status"] == "PASS"
    assert result["xml10_characters"] == "valid"
    assert result["well_formed"] is True
    assert result["checked_text_count"] == 1
    assert len(result["input_sha256"]) == 64
    assert title not in json.dumps(result)


@pytest.mark.parametrize(
    "xml",
    [
        '<Catalog xmlns="urn:catalog"><Title><![CDATA[literal <!DOCTYPE Catalog>]]></Title></Catalog>',
        '<!-- literal <!DOCTYPE Catalog> -->'
        '<Catalog xmlns="urn:catalog"><Title>literal &lt;!DOCTYPE Catalog&gt;</Title></Catalog>',
        '<Catalog xmlns="urn:catalog"><!-- literal <!DOCTYPE Catalog> -->'
        '<Title>literal &lt;!DOCTYPE Catalog&gt;</Title></Catalog>',
        '<?note literal <!DOCTYPE Catalog>?>'
        '<Catalog xmlns="urn:catalog"><Title>literal &lt;!DOCTYPE Catalog&gt;</Title></Catalog>',
    ],
)
def test_xml_output_oracle_accepts_doctype_spelling_as_literal_data(xml):
    result = evaluate_xml_output(xml, _xml_expectations("literal <!DOCTYPE Catalog>"))
    assert result["status"] == "PASS"
    assert result["well_formed"] is True
    assert result["round_trip"] == "verified"
    assert result["checked_text_count"] == 1
    assert "literal <!DOCTYPE Catalog>" not in json.dumps(result)


@pytest.mark.parametrize(
    "declaration",
    [
        '<!DOCTYPE Catalog>',
        '<!DOCTYPE Catalog [<!ENTITY title "safe">]>',
        '<!DOCTYPE Catalog SYSTEM "file:///unavailable-catalog.dtd">',
        '<!DOCTYPE Catalog PUBLIC "-//Example//DTD Catalog//EN" "https://example.invalid/catalog.dtd">',
        '<!DOCTYPE Catalog [invalid subset syntax]>',
    ],
)
def test_xml_output_oracle_rejects_actual_dtd_before_processing_subset(declaration):
    result = evaluate_xml_output(
        declaration + '<Catalog xmlns="urn:catalog"><Title>&title;</Title></Catalog>',
        _xml_expectations("safe"),
    )
    assert result["status"] == "UNSUPPORTED_DTD"
    assert result["well_formed"] is None
    assert result["round_trip"] == "unverified"
    assert result["checked_text_count"] == result["checked_attribute_count"] == 0


def test_xml_output_oracle_checks_namespaced_attribute_value():
    xml = (
        '<Catalog xmlns="urn:catalog" xmlns:m="urn:meta">'
        '<Item m:code="A&amp;B"/></Catalog>'
    )
    expectations = {
        "root_tag": "{urn:catalog}Catalog",
        "texts": [],
        "attributes": [{
            "tag": "{urn:catalog}Item", "occurrence": 0,
            "attribute": "{urn:meta}code", "expected_value": "A&B",
        }],
    }
    passed = evaluate_xml_output(xml, expectations)
    assert passed["status"] == "PASS"
    assert passed["checked_attribute_count"] == 1
    expectations["attributes"][0]["expected_value"] = "A&B!"
    failed = evaluate_xml_output(xml, expectations)
    assert failed["status"] == "ROUND_TRIP_MISMATCH"
    assert failed["mismatch_kind"] == "attribute_value"


@pytest.mark.parametrize(
    ("attribute", "expected_value", "status"),
    [
        ('code="true"', "true", "PASS"),
        ('code="false"', "false", "PASS"),
        ("code", "true", "MALFORMED_XML"),
    ],
    ids=["quoted-true", "quoted-false", "bare-attribute"],
)
def test_xml_output_oracle_preserves_boolean_like_string_attributes(
    attribute, expected_value, status,
):
    # String values must remain quoted XML attributes, not HTML-style booleans.
    xml = (
        '<Catalog xmlns="urn:catalog">'
        f'<Title {attribute}>fixture-payload</Title></Catalog>'
    )
    expectations = _xml_expectations("fixture-payload")
    expectations["attributes"] = [{
        "tag": "{urn:catalog}Title", "occurrence": 0,
        "attribute": "code", "expected_value": expected_value,
    }]
    result = evaluate_xml_output(xml, expectations)
    assert result["status"] == status
    assert result["well_formed"] is (status == "PASS")
    assert result["round_trip"] == ("verified" if status == "PASS" else "unverified")
    assert result["checked_text_count"] == result["checked_attribute_count"] == (
        1 if status == "PASS" else 0
    )
    assert result["input_sha256"] == hashlib.sha256(xml.encode("utf-8")).hexdigest()
    assert "fixture-payload" not in json.dumps(result)


@pytest.mark.parametrize(
    ("serialized_text", "status"),
    [
        ("left\r\nright", "ROUND_TRIP_MISMATCH"),
        ("left&#xD;\nright", "PASS"),
    ],
    ids=["raw-crlf-normalizes", "referenced-cr-preserves-value"],
)
def test_xml_output_oracle_distinguishes_newline_normalization_from_exact_value(
    serialized_text, status,
):
    xml = f'<Catalog xmlns="urn:catalog"><Title>{serialized_text}</Title></Catalog>'
    result = evaluate_xml_output(xml, _xml_expectations("left\r\nright"))
    assert result["status"] == status
    assert result["well_formed"] is True
    assert result["round_trip"] == ("verified" if status == "PASS" else "mismatch")
    assert result["checked_text_count"] == (1 if status == "PASS" else 0)
    if status == "ROUND_TRIP_MISMATCH":
        assert result["mismatch_kind"] == "text_or_structure"
    assert result["input_sha256"] == hashlib.sha256(xml.encode("utf-8")).hexdigest()
    assert "left" not in json.dumps(result)
    assert "right" not in json.dumps(result)


@pytest.mark.parametrize("bad_character", ["\x00", "\x01", "\ud800", "\ufffe"])
def test_xml_output_oracle_rejects_forbidden_xml10_characters(bad_character):
    result = evaluate_xml_output(
        f"<Catalog><Title>{bad_character}</Title></Catalog>",
        _xml_expectations("safe"),
    )
    assert result["status"] == "INVALID_XML10_CHARACTER"
    assert result["xml10_characters"] == "invalid"
    assert result["round_trip"] == "unverified"


def test_xml_output_oracle_reports_unencodable_character_without_content():
    xml = "<Catalog><Title>prefix\ud800private</Title></Catalog>"
    result = evaluate_xml_output(xml, _xml_expectations("safe"))
    assert result["status"] == "INVALID_XML10_CHARACTER"
    assert result["input_sha256"] is None
    assert result["invalid_character"] == {"codepoint": "U+D800", "offset": xml.index("\ud800")}
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(
    ("xml", "status"),
    [
        ("<Catalog><Title>&#x1;</Title></Catalog>", "MALFORMED_XML"),
        ('<!doctype Catalog><Catalog/>', "MALFORMED_XML"),
        ('<! DOCTYPE Catalog><Catalog/>', "MALFORMED_XML"),
        ('<Catalog><Title>literal <!DOCTYPE Catalog></Title></Catalog>', "MALFORMED_XML"),
        ('<!DOCTYPE Catalog [<!ENTITY x "value">]><Catalog/>', "UNSUPPORTED_DTD"),
        ('<?xml version="1.1"?><Catalog/>', "UNSUPPORTED_XML_VERSION"),
        ('<?xml version="1.0" encoding="UTF-16"?><Catalog/>', "UNSUPPORTED_ENCODING_DECLARATION"),
        ("<Catalog><Title>broken</Catalog>", "MALFORMED_XML"),
    ],
)
def test_xml_output_oracle_rejects_unsafe_or_malformed_documents(xml, status):
    assert evaluate_xml_output(xml, _xml_expectations("safe"))["status"] == status


@pytest.mark.parametrize(
    ("xml", "expectations", "mismatch_kind"),
    [
        ('<Catalog><Title>safe</Title></Catalog>', _xml_expectations("safe"), "root_tag"),
        ('<Catalog xmlns="urn:catalog"/>', _xml_expectations("safe"), "missing_text_node"),
        (
            '<Catalog xmlns="urn:catalog"><Title>changed</Title></Catalog>',
            _xml_expectations("safe"), "text_or_structure",
        ),
        (
            '<Catalog xmlns="urn:catalog"><Title>safe<Injected/></Title></Catalog>',
            _xml_expectations("safe"), "text_or_structure",
        ),
    ],
)
def test_xml_output_oracle_rejects_namespace_value_and_structure_drift(
    xml, expectations, mismatch_kind,
):
    result = evaluate_xml_output(xml, expectations)
    assert result["status"] == "ROUND_TRIP_MISMATCH"
    assert result["mismatch_kind"] == mismatch_kind


def test_xml_output_oracle_requires_bounded_explicit_expectations():
    xml = '<Catalog xmlns="urn:catalog"><Title>safe</Title></Catalog>'
    assert evaluate_xml_output(xml)["status"] == "NEEDS_EXPECTATIONS"
    assert evaluate_xml_output(xml, {"root_tag": "Catalog", "texts": []})[
        "status"
    ] == "INVALID_EXPECTATIONS"
    assert evaluate_xml_output(xml, _xml_expectations("x" * (MAX_XML_CHARACTERS + 1)))[
        "status"
    ] == "INVALID_EXPECTATIONS"
    assert evaluate_xml_output("x" * (MAX_XML_CHARACTERS + 1))["status"] == "INVALID_INPUT"


def test_xml_output_oracle_cli_is_read_only_and_fail_closed(tmp_path):
    xml_path = tmp_path / "export.xml"
    expected_path = tmp_path / "expected.json"
    xml_path.write_text(
        '<Catalog xmlns="urn:catalog"><Title>A &amp; B</Title></Catalog>',
        encoding="utf-8",
    )
    expected_path.write_text(json.dumps(_xml_expectations("A & B")), encoding="utf-8")
    command = [
        sys.executable, "-m", "tools.validate_xml_format_output",
        "--xml-file", str(xml_path), "--expectations-file", str(expected_path),
    ]
    passed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert passed.returncode == 0, passed.stderr
    assert json.loads(passed.stdout)["status"] == "PASS"
    assert "A & B" not in passed.stdout
    assert xml_path.read_text(encoding="utf-8").endswith("</Catalog>")
    xml_path.write_text("<Catalog><Title>\x01</Title></Catalog>", encoding="utf-8")
    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert failed.returncode == 2
    assert json.loads(failed.stdout)["status"] == "INVALID_XML10_CHARACTER"


@pytest.mark.parametrize("actual_dtd", [False, True])
def test_xml_output_oracle_cli_distinguishes_literal_and_actual_dtd(tmp_path, actual_dtd):
    title = "literal <!DOCTYPE Catalog>"
    xml = (
        ('<!DOCTYPE Catalog>' if actual_dtd else '')
        + '<Catalog xmlns="urn:catalog"><Title>'
        '<![CDATA[literal <!DOCTYPE Catalog>]]></Title></Catalog>'
    ).encode("utf-8")
    xml_path = tmp_path / "export.xml"
    expectations_path = tmp_path / "expected.json"
    xml_path.write_bytes(xml)
    expectations_bytes = json.dumps(_xml_expectations(title)).encode("utf-8")
    expectations_path.write_bytes(expectations_bytes)
    result = subprocess.run(
        [
            sys.executable, "-m", "tools.validate_xml_format_output",
            "--xml-file", str(xml_path), "--expectations-file", str(expectations_path),
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    report = json.loads(result.stdout)
    assert result.returncode == (2 if actual_dtd else 0), result.stderr
    assert report["status"] == ("UNSUPPORTED_DTD" if actual_dtd else "PASS")
    assert report["source_bytes_sha256"] == hashlib.sha256(xml).hexdigest()
    assert title not in result.stdout
    assert xml_path.read_bytes() == xml
    assert expectations_path.read_bytes() == expectations_bytes


@pytest.mark.parametrize(
    ("serialized_text", "status"),
    [
        ("left\r\nright", "ROUND_TRIP_MISMATCH"),
        ("left&#xD;\nright", "PASS"),
    ],
    ids=["raw-crlf-normalizes", "referenced-cr-preserves-value"],
)
def test_xml_output_oracle_cli_preserves_newline_evidence(tmp_path, serialized_text, status):
    # Byte I/O prevents the CLI from hiding a lost CR before the parser checks it.
    xml = (
        f'<Catalog xmlns="urn:catalog"><Title>{serialized_text}</Title></Catalog>'
    ).encode("utf-8")
    expectations = json.dumps(_xml_expectations("left\r\nright")).encode("utf-8")
    xml_path = tmp_path / "export.xml"
    expectations_path = tmp_path / "expected.json"
    xml_path.write_bytes(xml)
    expectations_path.write_bytes(expectations)
    result = subprocess.run(
        [
            sys.executable, "-m", "tools.validate_xml_format_output",
            "--xml-file", str(xml_path), "--expectations-file", str(expectations_path),
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    report = json.loads(result.stdout)
    assert result.returncode == (0 if status == "PASS" else 2), result.stderr
    assert report["status"] == status
    assert report["well_formed"] is True
    assert report["round_trip"] == ("verified" if status == "PASS" else "mismatch")
    assert report["source_bytes_sha256"] == hashlib.sha256(xml).hexdigest()
    assert report["input_sha256"] == hashlib.sha256(xml).hexdigest()
    assert "left" not in result.stdout and "right" not in result.stdout
    assert xml_path.read_bytes() == xml
    assert expectations_path.read_bytes() == expectations


def test_xml_advisory_points_to_explicit_oracle_without_proof_promotion(tmp_path):
    source = (
        'new Response(`<Root>${value}</Root>`, '
        '{headers: {"Content-Type": "application/xml"}})'
    )
    evidence = _structured_output_evidence([_sequence(tmp_path, source)])
    finding = analyze_frontier_file(
        "MAIN", "feed.ts", source,
        {"structured_output_evidence": evidence,
         "hash": hashlib.sha256(source.encode("utf-8")).hexdigest()},
    )["findings"][0]
    assert "tools.validate_xml_format_output" in finding["recommended_action"]
    assert "XML 1.0" in finding["recommended_action"]
    assert finding["confidence"] == "needs_runtime_proof"
    assert _refactor_plan([finding])["total_tasks"] == 0
