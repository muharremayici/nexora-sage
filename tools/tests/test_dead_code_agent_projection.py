from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import inspect_target
from tools.core import dead_code_intent_review
from tools.engines.dead_code_detector import DeadCodeDetector
from tools.mcp import server


def _create_inspection_db(raw_dir: Path, dead_code_payload: dict | None) -> None:
    raw_dir.mkdir()
    with sqlite3.connect(raw_dir / "codemaps.db") as conn:
        conn.executescript(
            """
            CREATE TABLE projects (project_key TEXT PRIMARY KEY, path TEXT);
            CREATE TABLE files (file_id INTEGER PRIMARY KEY, project_key TEXT, rel_path TEXT);
            CREATE TABLE symbols (file_id INTEGER, name TEXT, type TEXT, line INTEGER, char INTEGER, end_line INTEGER, source_lines TEXT);
            CREATE TABLE dependencies (source_file_id INTEGER);
            CREATE TABLE findings (finding_id TEXT, file_id INTEGER, engine_name TEXT, severity TEXT, code TEXT, message TEXT);
            CREATE TABLE state_payloads (name TEXT PRIMARY KEY, payload TEXT, payload_sha TEXT, source_mtime REAL, updated_at TEXT);
            INSERT INTO projects VALUES ('MAIN', 'src');
            INSERT INTO files VALUES (1, 'MAIN', 'feature/CharactersTab.tsx');
            INSERT INTO findings VALUES (
                'finding-1', 1, 'audit_report', 'enforced', 'deep_imports',
                '{"rule":"deep_imports","detail":"Cross-lifecycle deep import"}'
            );
            """
        )
        if dead_code_payload is not None:
            conn.execute(
                "INSERT INTO state_payloads VALUES (?, ?, ?, ?, ?)",
                ("dead_code", json.dumps(dead_code_payload), "dead-code-sha", 1.0, "2026-08-08T00:00:00Z"),
            )


def _inspect(raw_dir: Path) -> dict:
    with patch.object(
        server,
        "_sqlite_file_context_from_raw",
        return_value=(
            "MAIN::feature/CharactersTab.tsx",
            {"repo_relative_path": "src/feature/CharactersTab.tsx"},
        ),
    ):
        payload = server._sqlite_file_inspection_payload(raw_dir, "src/feature/CharactersTab.tsx")
    assert payload is not None
    return payload


def _actionability(level: str, confidence: str) -> dict:
    return {
        "level": level,
        "score": 0.0,
        "policy": "dead_code_actionability_v1",
        "unusedness_confidence": confidence.lower(),
        "remediation_confidence": "unknown",
        "intent_decision_required": True,
        "mutation_proposed": False,
        "allowed_outcomes": ["delete", "complete_integration", "retain_contract", "unknown"],
        "why": "Graph evidence does not resolve intent.",
    }


def test_file_inspection_projects_high_and_medium_from_canonical_sqlite_dead_code(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    _create_inspection_db(
        raw_dir,
        {
            "meta": {"run_id": "run-21"},
            "items": [
                {
                    "project": "MAIN",
                    "file": "feature/CharactersTab.tsx",
                    "symbol": "orphaned",
                    "confidence": "HIGH",
                    "reason": "unreferenced_file_export",
                    "actionability": _actionability("manual_intent_decision", "HIGH"),
                },
                {
                    "project": "MAIN",
                    "file": "feature/CharactersTab.tsx",
                    "symbol": "partial",
                    "confidence": "MEDIUM",
                    "reason": "unconsumed_export_in_referenced_file",
                    "actionability": _actionability("review", "MEDIUM"),
                },
            ],
        },
    )
    payload = _inspect(raw_dir)

    assert payload["summary"]["dead_code_matches"] == 2
    assert payload["summary"]["audit_violations"] == 1
    assert payload["audit_violations"][0]["rule"] == "deep_imports"
    assert payload["summary"]["dead_code_matches_status"] == "AVAILABLE"
    assert [row["confidence"] for row in payload["dead_code_matches"]] == ["HIGH", "MEDIUM"]
    assert payload["dead_code_matches"][0]["actionability"]["remediation_confidence"] == "unknown"
    assert payload["meta"]["dead_code_artifact_identity"]["content_fingerprint"] == "sqlite:dead-code-sha"


def test_file_inspection_reports_unknown_when_dead_code_projection_is_missing(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    _create_inspection_db(raw_dir, None)
    payload = _inspect(raw_dir)

    assert payload["meta"]["evidence_status"] == "PARTIAL"
    assert payload["summary"]["dead_code_matches"] is None
    assert payload["summary"]["dead_code_matches_status"] == "UNKNOWN"
    assert "dead_code_matches: UNKNOWN" in inspect_target.render_agent_inspection_brief(payload)


def test_file_inspection_distinguishes_available_zero_from_unknown(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    _create_inspection_db(raw_dir, {"meta": {"run_id": "empty-run"}, "items": []})
    payload = _inspect(raw_dir)

    assert payload["meta"]["evidence_status"] == "COMPLETE"
    assert payload["summary"]["dead_code_matches"] == 0
    assert payload["summary"]["dead_code_matches_status"] == "AVAILABLE"
    brief = inspect_target.render_agent_inspection_brief(payload)
    assert "dead_code_matches: 0" in brief
    assert "dead_code_matches: UNKNOWN" not in brief


def test_actual_detector_request_survives_sqlite_to_agent_projection(tmp_path: Path) -> None:
    raw_dir = tmp_path / ".raw"
    actionability = DeadCodeDetector._actionability_profile("HIGH", "unreferenced_file_export")
    _create_inspection_db(raw_dir, {"meta": {"run_id": "intent-review-fixture"}, "items": [{
        "project": "MAIN", "file": "feature/CharactersTab.tsx", "symbol": "performExport",
        "confidence": "HIGH", "reason": "unreferenced_file_export", "actionability": actionability}]})
    payload = _inspect(raw_dir)
    compact = inspect_target._compact_dead_row(payload["dead_code_matches"][0])
    request = compact["actionability"]["intent_review"]
    assert request == actionability["intent_review"]
    assert request["status"] == "evidence_required"
    assert request["correlation_status"] == "not_evaluated"
    assert request["integration_status"] == "unknown"
    assert request["mutation_proposed"] is False
    assert len(request["questions"]) == 3
    assert any("contrary" in question for question in request["questions"])
    assert payload["summary"]["dead_code_matches"] == 1
    assert payload["summary"]["audit_violations"] == 1
    brief = inspect_target.render_agent_inspection_brief({**payload, "dead_code_matches": [compact]})
    assert '"integration_status": "unknown"' in brief
    assert '"correlation_status": "not_evaluated"' in brief
    assert '"mutation_proposed": false' in brief


@pytest.mark.parametrize("changes", [
    {"status": "observed"}, {"correlation_status": "proven"},
    {"integration_status": "incomplete"}, {"mutation_proposed": True}, {"mutation_proposed": 0},
    {"policy_sha256": "0" * 64}, {"questions": ["Delete this code."]},
    {"extra_authority": True},
])
def test_stored_guidance_cannot_be_promoted_or_resealed(changes: dict) -> None:
    request = dead_code_intent_review.intent_review_request()
    request.update(changes)
    projected = dead_code_intent_review.project_intent_review_request(request)
    assert projected["status"] == "unavailable"
    assert projected["correlation_status"] == "not_evaluated"
    assert projected["integration_status"] == "unknown"
    assert projected["mutation_proposed"] is False
    assert projected["questions"] == []


def test_legacy_dead_code_does_not_gain_a_new_positive_request() -> None:
    row = {"project": "MAIN", "file": "orphan.ts", "symbol": "performExport",
           "actionability": _actionability("manual_intent_decision", "HIGH")}
    request = inspect_target._compact_dead_row(row)["actionability"]["intent_review"]
    assert request["status"] == "unavailable"
    assert request["questions"] == []


@pytest.mark.parametrize("policy", [None, {}, {"id": "invalid", "limits": {}},
    {"id": "invalid", "limits": {"maximum_questions": True, "maximum_question_characters": 10},
     "questions": ["Question"]},
    {"id": "invalid", "limits": {"maximum_questions": 1, "maximum_question_characters": 2},
     "questions": ["over budget"]},
    {"id": "invalid", "limits": {"maximum_questions": 1, "maximum_question_characters": 10},
     "questions": ["one", "two"]},
])
def test_missing_invalid_or_over_budget_policy_is_unavailable(policy) -> None:
    with patch.object(dead_code_intent_review, "load_json_object_strict_cached",
                      return_value={"intent_review_request": policy}):
        request = dead_code_intent_review.intent_review_request()
    assert request["status"] == "unavailable"
    assert request["questions"] == []
    assert request["integration_status"] == "unknown"
    assert request["mutation_proposed"] is False


def test_request_is_detached_and_policy_change_invalidates_stored_guidance_and_cache() -> None:
    request = dead_code_intent_review.intent_review_request()
    detector = object.__new__(DeadCodeDetector)
    old_signature = detector._doctrine_signature()
    policy_path = Path(__file__).resolve().parents[2] / "config/dead_code_language_agnostic_core_contract.json"
    changed = json.loads(policy_path.read_text(encoding="utf-8"))
    changed["intent_review_request"]["questions"][0] = "Inspect changed policy guidance."
    with patch.object(dead_code_intent_review, "load_json_object_strict_cached", return_value=changed):
        assert detector._doctrine_signature() != old_signature
        assert dead_code_intent_review.project_intent_review_request(request)["status"] == "unavailable"
    request["questions"].append("Local mutation")
    assert "Local mutation" not in dead_code_intent_review.intent_review_request()["questions"]


def test_true_obsolete_and_potentially_unwired_exports_are_not_distinguished_by_unusedness() -> None:
    for reason in ("unreferenced_file_export", "constant_surface_unreferenced_file_export"):
        profile = DeadCodeDetector._actionability_profile("HIGH", reason)
        assert profile["remediation_confidence"] == "unknown"
        assert profile["intent_review"]["integration_status"] == "unknown"
        assert profile["mutation_proposed"] is False
    obsolete = DeadCodeDetector._actionability_profile("HIGH", "deprecated_empty_export_surface")
    assert obsolete["remediation_confidence"] == "conditional"
    assert "intent_review" not in obsolete
    assert obsolete["mutation_proposed"] is False
    assert "intent_review" not in inspect_target._compact_dead_row(
        {"actionability": obsolete})["actionability"]


@pytest.mark.parametrize("consumed,retained", [(False, False), (True, False), (False, True)])
def test_actual_export_analysis_keeps_contract_and_after_consumption_controls(
        tmp_path: Path, consumed: bool, retained: bool) -> None:
    detector = DeadCodeDetector(cache_path=tmp_path / "project-cache.json")
    detector.projects = {"MAIN": tmp_path}
    key = ("src/orphan.ts", "performExport")
    analysis = detector._analyze_project_exports(project="MAIN", project_data={"files": {
        key[0]: {"language": "typescript", "parser_evidence": {"status": "observed",
            "parser_kind": "typescript_compiler_api", "semantic_depth": "logic_normalized"},
            "exports": [{"name": key[1], "type": "Function"}],
            "symbols": [{"name": key[1], "exported": True, "type": "Function"}]}}},
        imported_files_for_project=set(), namespace_imported_files_for_project=set(),
        propagated_consumed_for_project={key} if consumed else set(),
        public_export_chain_files=set(), public_export_chain_symbols={key} if retained else set(),
        all_imported_names=set(), symbol_usage_files_for_project={})
    if consumed or retained:
        assert analysis["dead"] == []
    else:
        profile = analysis["dead"][0]["actionability"]
        assert profile["intent_review"]["status"] == "evidence_required"
        assert profile["intent_review"]["integration_status"] == "unknown"
        assert profile["level"] == "manual_intent_decision"
    if retained:
        assert analysis["compatibility_exclusions"][0]["contract_class"] == "PUBLIC_PACKAGE_API"


def test_intent_review_schema_accepts_request_and_refuses_detection_authority() -> None:
    import jsonschema
    from tools.core.artifact_validator import _validate

    schema_path = Path(__file__).resolve().parents[2] / "config/schemas/dead_code.schema.json"
    definitions = json.loads(schema_path.read_text(encoding="utf-8"))["$defs"]
    schema = {"$ref": "#/$defs/actionability", "$defs": definitions}
    profile = DeadCodeDetector._actionability_profile("HIGH", "unreferenced_file_export")
    jsonschema.validate(profile, schema)
    native_errors = []
    _validate(profile, schema, schema, ["actionability"], native_errors)
    assert native_errors == []
    promoted = deepcopy(profile)
    promoted["intent_review"]["integration_status"] = "incomplete"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(promoted, schema)
    _validate(promoted, schema, schema, ["actionability"], native_errors)
    assert native_errors
    # The actual shared validator uses Python equality for const. Each dead-code
    # refusal marker must independently declare boolean type so zero is refused.
    for definition in (definitions["actionability"], definitions["intentReviewRequest"],
                       definitions["contractTriageItem"]["allOf"][1],
                       definitions["unresolvedReachabilityItem"]):
        marker = definition["properties"]["mutation_proposed"]
        errors = []
        _validate(False, marker, schema, ["mutation_proposed"], errors)
        assert errors == []
        _validate(0, marker, schema, ["mutation_proposed"], errors)
        assert errors


def test_review_request_stays_within_existing_brief_budget_for_six_candidates() -> None:
    from tools.core.agent_packet_budget import DEFAULT_AGENT_PACKET_BUDGET_TOKENS, estimate_tokens

    profile = DeadCodeDetector._actionability_profile("HIGH", "unreferenced_file_export")
    rows = [inspect_target._compact_dead_row({"project": "MAIN", "file": "feature/export.ts",
        "symbol": f"unconsumed{index}", "confidence": "HIGH", "reason": "unreferenced_file_export",
        "actionability": profile}) for index in range(6)]
    brief = inspect_target.render_agent_inspection_brief({"dead_code_matches": rows,
        "summary": {"dead_code_matches": 6, "dead_code_matches_status": "AVAILABLE"}})
    assert estimate_tokens(brief) <= DEFAULT_AGENT_PACKET_BUDGET_TOKENS
    assert brief.count('"integration_status": "unknown"') == 6


_WIRING_IMPORT = "import {performExport as selected} from './service';\r\n"
_WIRING_EMPTY = "export function Panel() { return <button onClick={() => { /* deliberate? unknown */ }} />; }\r\n"


@pytest.fixture(scope="module")
def wiring_observations_inputs(tmp_path_factory):
    import subprocess
    from tools.core.wiring_source_observations import normalize_wiring_source_observations

    root = Path(__file__).resolve().parents[2]
    directory = tmp_path_factory.mktemp("wiring-native")
    cases = {
        "positive": _WIRING_IMPORT + _WIRING_EMPTY,
        "after_wiring": _WIRING_IMPORT + _WIRING_EMPTY.replace("/* deliberate? unknown */", "selected();"),
        "retained_export": _WIRING_IMPORT + "export {selected};\r\n" + _WIRING_EMPTY,
        "obsolete_unknown": "// Intentionally obsolete? This comment cannot settle intent.\r\n" + _WIRING_IMPORT + _WIRING_EMPTY,
        "pending_comment": "// TODO: connect the export action to the button.\r\n" + _WIRING_IMPORT + _WIRING_EMPTY,
        "retired_comment": "// The old export feature is retired; do not reconnect it.\r\n" + _WIRING_IMPORT + _WIRING_EMPTY,
        "deliberate_noop": "// The button intentionally does nothing in this preview.\r\n" + _WIRING_IMPORT + _WIRING_EMPTY,
        "dynamic_string": _WIRING_IMPORT + "const name = 'selected'; const action = registry[name];\r\n" + _WIRING_EMPTY,
        "foreign_module": _WIRING_IMPORT.replace("'./service'", "'unresolved-external-package'") + _WIRING_EMPTY,
        "unrelated_owner": _WIRING_IMPORT + "export function unrelatedFeature() { return 1; }\r\n" + _WIRING_EMPTY,
        "consumed_elsewhere": _WIRING_IMPORT + "export function backgroundTask() { selected(); }\r\n" + _WIRING_EMPTY,
        "unreachable_use": _WIRING_IMPORT + "if (false) { selected(); }\r\n" + _WIRING_EMPTY,
        "wired_with_other_empty": _WIRING_IMPORT + _WIRING_EMPTY.replace("/* deliberate? unknown */", "selected();")
            + "\r\nexport const Other = () => <button onClick={() => {}} />;",
        "both_kinds_partial": _WIRING_IMPORT + "export const Panel = () => <>"
            + "<button onClick={() => {}} />" * 70 + "</>;",
        "display_hidden_kind": "\r\n".join(f"import {{action{i}}} from './service';" for i in range(8)) + _WIRING_EMPTY,
        "shadow": _WIRING_IMPORT + "function other(selected: any) {return selected;}\r\n" + _WIRING_EMPTY,
        "type_use": _WIRING_IMPORT + "type T = typeof selected;\r\n" + _WIRING_EMPTY,
        "write": _WIRING_IMPORT + "selected = replacement;\r\n" + _WIRING_EMPTY,
        "property_name": _WIRING_IMPORT + "const data = {selected: 1};\r\n" + _WIRING_EMPTY,
        "type_only": _WIRING_IMPORT.replace("import {", "import type {") + _WIRING_EMPTY,
        "specifier_type_only": _WIRING_IMPORT.replace("{performExport", "{type performExport") + _WIRING_EMPTY,
        "namespace": "import * as selected from './service';\r\n" + _WIRING_EMPTY,
        "default": "import selected from './service';\r\n" + _WIRING_EMPTY,
        "no_ui": _WIRING_IMPORT + "export const Panel = () => null;",
        "nonempty": _WIRING_IMPORT + _WIRING_EMPTY.replace("/* deliberate? unknown */", "return;"),
        "async": _WIRING_IMPORT + _WIRING_EMPTY.replace("onClick={()", "onClick={async ()"),
        "parameter": _WIRING_IMPORT + _WIRING_EMPTY.replace("() =>", "(event) =>"),
        "wrapped": _WIRING_IMPORT + _WIRING_EMPTY.replace("onClick={() => { /* deliberate? unknown */ }}", "onClick={wrap(() => {})}"),
        "spread": _WIRING_IMPORT + _WIRING_EMPTY.replace("<button ", "<button {...props} "),
        "duplicate": _WIRING_IMPORT + _WIRING_EMPTY.replace("<button ", "<button onClick={other} "),
        "custom_component": _WIRING_IMPORT + _WIRING_EMPTY.replace("<button ", "<Button "),
        "non_event_prop": _WIRING_IMPORT + _WIRING_EMPTY.replace("onClick=", "render="),
        "parse_error": _WIRING_IMPORT + "export function Broken( { return <button onClick={() => {}} />;",
        "cap": "export const Panel = () => <>" + "<button onClick={() => {}} />" * 70 + "</>;",
        "node_cap": _WIRING_IMPORT + "const list = [" + "0," * 11000 + "];" + _WIRING_EMPTY,
        "byte_cap": "//" + "a" * 1048576 + "\r\n" + _WIRING_IMPORT + _WIRING_EMPTY,
        "no_facts": "export const ready = 1;",
    }
    paths, sources = [], {}
    for name, source in cases.items():
        source = "\ufeff// 😀 exact UTF-16 / UTF-8 coordinates\r\n" + source
        path = directory / f"{name}.tsx"
        path.write_bytes(source.encode("utf-8"))
        paths.append(str(path))
        sources[name] = source
    # A resolver must neither evaluate nor import the target's dependency.
    (directory / "service.ts").write_text("throw new Error('NEVER_EXECUTE_TARGET');", encoding="utf-8")
    completed = subprocess.run(["node", str(root / "tools/engines/ast_sequencer.cjs"), "--batch-json", *paths],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert completed.returncode == 0, completed.stderr
    raw = json.loads(completed.stdout)
    return {name: (sources[name], raw[str(directory / f"{name}.tsx")],
                   normalize_wiring_source_observations(raw[str(directory / f"{name}.tsx")], sources[name])) for name in cases}


def test_wiring_observations_positive_is_source_facts_not_an_intent_join(wiring_observations_inputs):
    from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION
    from tools.core.wiring_source_observations import IMPORT, CALLBACK, BOUNDARIES

    source, _, lane = wiring_observations_inputs["positive"]
    assert lane["status"] == "observed" and lane["record_count"] == 2
    assert lane["ast_contract_version"] == AST_CONTRACT_VERSION
    assert lane["offset_unit"] == "utf8_bytes"
    assert {row["kind"] for row in lane["records"]} == {IMPORT, CALLBACK}
    assert all(lane[key] == value for key,value in BOUNDARIES.items())
    data = source.encode("utf-8")
    for row in lane["records"]:
        if row["kind"] == IMPORT:
            assert row["source"] == "./service" and row["imported_name"] == "performExport"
            selected = row["spans"]["local_binding"]
            assert data[selected["start"]:selected["end"]] == b"selected"
        else:
            assert row["tag"] == "button" and row["prop"] == "onClick"
            selected = row["spans"]["callback_body"]
            assert data[selected["start"]:selected["end"]] == b"{ /* deliberate? unknown */ }"
    assert not any(key in lane for key in ("import_to_callback", "intent", "confidence", "delete"))


@pytest.mark.parametrize("name", ["retained_export", "shadow", "type_use", "write", "property_name",
                                  "type_only", "specifier_type_only", "namespace", "default"])
def test_wiring_observations_contrary_or_outside_import_grammar_withholds_fact(wiring_observations_inputs, name):
    from tools.core.wiring_source_observations import CALLBACK
    lane = wiring_observations_inputs[name][2]
    assert lane["status"] == "observed"
    assert [row["kind"] for row in lane["records"]] == [CALLBACK]
    assert lane["integration_status"] == "unknown" and lane["mutation_proposed"] is False


@pytest.mark.parametrize("name", ["no_ui", "nonempty", "async", "parameter", "wrapped", "spread", "duplicate", "custom_component", "non_event_prop"])
def test_wiring_observations_empty_callback_grammar_is_not_a_name_heuristic(wiring_observations_inputs, name):
    from tools.core.wiring_source_observations import IMPORT
    lane = wiring_observations_inputs[name][2]
    assert lane["status"] == "observed" and [row["kind"] for row in lane["records"]] == [IMPORT]


def test_wiring_observations_after_wiring_disappears_but_does_not_claim_runtime(wiring_observations_inputs):
    lane = wiring_observations_inputs["after_wiring"][2]
    assert lane["status"] == "observed" and lane["records"] == []
    assert lane["runtime_execution"] == "not_established" and lane["integration_status"] == "unknown"
    obsolete = wiring_observations_inputs["obsolete_unknown"][2]
    assert obsolete["record_count"] == 2 and obsolete["correlation_status"] == "not_evaluated"
    assert obsolete["integration_status"] == "unknown"  # Comments don't prove obsolete or unfinished.


@pytest.mark.parametrize("name", ["parse_error", "node_cap", "byte_cap"])
def test_wiring_observations_scan_failure_is_unavailable_not_clean(wiring_observations_inputs, name):
    lane = wiring_observations_inputs[name][2]
    assert lane["status"] == "unavailable" and lane["records"] == [] and lane["reason"]


def test_wiring_observations_record_cap_discloses_partial(wiring_observations_inputs):
    lane = wiring_observations_inputs["cap"][2]
    assert lane["status"] == "partial" and lane["record_count"] == 64 and lane["omitted"] == 6
    assert lane["reason"] == "record_budget_exceeded"


@pytest.mark.parametrize("damage", ["missing", "source", "meta_hash", "duplicate_meta", "parser", "parser_kind",
    "version", "unit", "count", "bool_count", "unknown_key", "promote", "zero_mutation", "foreign_hash",
    "duplicate_record", "line", "coordinate", "token", "spans", "roles", "source_token", "body", "partial"])
def test_wiring_observations_missing_stale_or_promoted_metadata_is_refused(wiring_observations_inputs, damage):
    from tools.core.wiring_source_observations import normalize_wiring_source_observations
    source, original, _ = wiring_observations_inputs["positive"]
    raw = deepcopy(original)
    meta = next(row for row in raw if row.get("name") == "__file_meta__")
    lane = meta["wiringSourceObservations"]
    first = lane["records"][0]
    if damage == "missing": meta.pop("wiringSourceObservations")
    elif damage == "source": source += "\r\n// changed after parse"
    elif damage == "meta_hash": meta["features"] = [v for v in meta["features"] if not v.startswith("Hash:")]
    elif damage == "duplicate_meta": raw.append(deepcopy(meta))
    elif damage == "parser": meta["parserStatus"] = "degraded"
    elif damage == "parser_kind": meta["parserKind"] = "text_regex"
    elif damage == "version": lane["version"] = "legacy"
    elif damage == "unit": lane["offset_unit"] = "utf8_bytes"
    elif damage == "count": lane["record_count"] += 1
    elif damage == "bool_count": lane["omitted"] = False
    elif damage == "unknown_key": lane["detected_wiring"] = True
    elif damage == "promote": lane["integration_status"] = "incomplete"
    elif damage == "zero_mutation": lane["mutation_proposed"] = 0
    elif damage == "foreign_hash": lane["source_hash"] = "0" * 64
    elif damage == "duplicate_record": lane["records"].append(deepcopy(first)); lane["record_count"] += 1
    elif damage == "line": first["spans"]["local_binding"]["line"] += 1
    elif damage == "coordinate": first["spans"]["local_binding"]["start"] = 1
    elif damage == "token": first["local_name"] = "other"
    elif damage == "spans": first["spans"]["local_binding"] = deepcopy(first["spans"]["module_source"])
    elif damage == "roles": first["spans"].pop("import_binding")
    elif damage == "source_token": first["source"] = "./foreign"
    elif damage == "body": lane["records"][1]["spans"]["callback_body"] = deepcopy(lane["records"][1]["spans"]["callback"])
    elif damage == "partial": lane["status"] = "partial"
    result = normalize_wiring_source_observations(raw, source)
    assert result["status"] == "unavailable" and result["records"] == []
    assert result["integration_status"] == "unknown" and result["mutation_proposed"] is False


def test_wiring_observations_normalization_is_detached_and_one_coordinate_batch(wiring_observations_inputs):
    from tools.core import wiring_source_observations as owner
    source, raw, _ = wiring_observations_inputs["positive"]
    with patch.object(owner, "_utf16_offsets_to_utf8_byte_offsets", wraps=owner._utf16_offsets_to_utf8_byte_offsets) as convert:
        result = owner.normalize_wiring_source_observations(raw, source)
    assert convert.call_count == 1
    result["records"][0]["local_name"] = "detached"
    assert owner.normalize_wiring_source_observations(raw, source)["records"][0]["local_name"] == "selected"


def test_wiring_observations_schema_is_executable_in_actual_shared_validator(wiring_observations_inputs):
    import jsonschema
    from tools.core.artifact_validator import _validate
    root = Path(__file__).resolve().parents[2]
    definitions = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))["$defs"]
    schema = {"$defs": definitions, "$ref": "#/$defs/wiring_source_observations"}
    for name in ("positive", "cap", "node_cap", "parse_error", "no_facts", "byte_cap"):
        lane = wiring_observations_inputs[name][2]
        jsonschema.validate(lane, schema)
        errors = []
        _validate(lane, schema, schema, ["observations"], errors)
        assert errors == []
    for key, value in (("mutation_proposed", 0), ("integration_status", "incomplete"),
                       ("correlation_status", "confirmed"), ("detection", True)):
        lane = deepcopy(wiring_observations_inputs["positive"][2])
        lane[key] = value
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(lane, schema)
        errors = []
        _validate(lane, schema, schema, ["observations"], errors)
        assert errors


def _wiring_view_input(wiring_observations_inputs, name="positive"):
    source, _, lane = wiring_observations_inputs[name]
    file_info = {"hash": lane.get("source_hash"), "ast_contract_version": lane["ast_contract_version"],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "wiring_source_observations": deepcopy(lane)}
    status = {"inside_root": True, "exists": True, "indexed": True, "source_snapshot_status": "ok",
        "drift_check_status": "match", "source_snapshot_hash": file_info["hash"], "target_ref": "MAIN::Panel.tsx"}
    return source, file_info, status


def test_wiring_view_separate_facts_keep_current_questions_and_source_ranges(wiring_observations_inputs):
    source, info, status = _wiring_view_input(wiring_observations_inputs)
    payload = {}
    inspect_target.project_wiring_source_observations(payload, info, status, snapshot_content=source)
    view = payload["wiring_source_observations"]
    assert view["status"] == "observed" and view["record_count"] == 2 and view["shown"] == 2
    assert view["source_binding"] == "snapshot_and_live_match" and view["omitted"] == view["producer_omitted"] == 0
    assert payload["wiring_review_request"] == dead_code_intent_review.intent_review_request()
    assert view["review_policy"] == payload["wiring_review_request"]["policy"]
    assert view["integration_status"] == "unknown" and view["correlation_status"] == "not_evaluated"
    assert view["mutation_proposed"] is False and view["edit_authority"] == "not_granted"
    for row in view["records"]:
        followup = row["source_followup"]
        assert followup["file_path"] == "MAIN::Panel.tsx" and followup["line_start"] <= followup["line_end"]
    view["records"][0]["local_name"] = "detached"
    assert info["wiring_source_observations"]["records"][0]["local_name"] == "selected"


@pytest.mark.parametrize("damage", ["drift", "missing_snapshot", "wrong_snapshot", "outside", "missing_file", "not_indexed",
    "truthy_indexed", "source_hash", "file_ast", "file_parser", "snapshot_hash", "promoted", "zero_mutation", "extra",
    "count", "body", "bad_byte", "kind", "old_ast", "role", "role_budget"])
def test_wiring_view_refuses_untrusted_or_changed_stored_observations(wiring_observations_inputs, damage):
    source, info, status = _wiring_view_input(wiring_observations_inputs)
    lane = info["wiring_source_observations"]
    if damage == "drift": status["drift_check_status"] = "drift"
    elif damage == "missing_snapshot": source = None
    elif damage == "wrong_snapshot": source += "\n// changed"
    elif damage == "outside": status["inside_root"] = False
    elif damage == "missing_file": status["exists"] = False
    elif damage == "not_indexed": status["indexed"] = False
    elif damage == "truthy_indexed": status["indexed"] = 1
    elif damage == "source_hash": info["hash"] = "0" * 64
    elif damage == "file_ast": info["ast_contract_version"] = "old"
    elif damage == "file_parser": info["parser_evidence"]["status"] = "degraded"
    elif damage == "snapshot_hash": status["source_snapshot_hash"] = "0" * 64
    elif damage == "promoted": lane["integration_status"] = "incomplete"
    elif damage == "zero_mutation": lane["mutation_proposed"] = 0
    elif damage == "extra": lane["linked_callback"] = True
    elif damage == "count": lane["record_count"] = 0
    elif damage == "body": lane["records"][1]["spans"]["callback_body"] = deepcopy(lane["records"][1]["spans"]["callback"])
    elif damage == "bad_byte": lane["records"][0]["spans"]["local_binding"]["start"] = 7  # inside UTF-8 emoji
    elif damage == "kind": lane["records"][0]["kind"] = []
    elif damage == "old_ast": info["ast_contract_version"] = lane["ast_contract_version"] = "old"
    elif damage == "role": lane["records"][0]["spans"].pop("import_binding")
    elif damage == "role_budget": lane["records"][0]["spans"].update({f"extra{i}": {} for i in range(10)})
    payload = {}
    inspect_target.project_wiring_source_observations(payload, info, status, snapshot_content=source)
    view = payload["wiring_source_observations"]
    assert view["status"] == "unavailable" and view["records"] == [] and view["record_count"] is None
    assert view["integration_status"] == "unknown" and view["mutation_proposed"] is False


@pytest.mark.parametrize("name", ["after_wiring", "retained_export", "obsolete_unknown", "cap", "parse_error"])
def test_wiring_view_after_wiring_contrary_partial_and_unavailable_are_distinct(wiring_observations_inputs, name):
    source, info, status = _wiring_view_input(wiring_observations_inputs, name)
    payload = {}
    inspect_target.project_wiring_source_observations(payload, info, status, snapshot_content=source)
    view = payload["wiring_source_observations"]
    assert view["integration_status"] == "unknown" and view["mutation_proposed"] is False
    if name == "after_wiring":
        assert view["status"] == "observed" and view["record_count"] == 0 and "wiring_review_request" not in payload
    elif name == "cap":
        assert view["status"] == "partial" and view["record_count"] == 64 and view["producer_omitted"] == 6
        assert view["shown"] + view["omitted"] == 64
    elif name == "parse_error":
        assert view["status"] == "unavailable" and view["record_count"] is None
        assert view["reason"] == "producer_evidence_unavailable"
    else:
        assert view["status"] == "observed" and view["record_count"] == (1 if name == "retained_export" else 2)


def test_wiring_view_preserves_legacy_and_prior_sections_with_shared_budgets(wiring_observations_inputs):
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
    source, info, status = _wiring_view_input(wiring_observations_inputs)
    legacy = {"older": "kept"}
    inspect_target.project_wiring_source_observations(legacy, {}, status, snapshot_content=source)
    assert legacy == {"older": "kept"}
    older = {"old_syntax": {"status": "observed", "records": [{"value": n} for n in range(6)]}}
    payload = deepcopy(older)
    inspect_target.project_wiring_source_observations(payload, info, status, snapshot_content=source, syntax_sections=older)
    assert payload["old_syntax"] == older["old_syntax"]
    view = payload["wiring_source_observations"]
    assert view["shown"] == 0 and view["omitted"] == 2
    syntax = {key:value for key,value in payload.items() if key != "wiring_review_request"}
    assert context_budget_profile(json.dumps(syntax), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    crowded = {"old_syntax": {"records": [], "reason": "x" * (COMPACT_AGENT_PACKET_TOKENS * 4)}}
    payload = deepcopy(crowded)
    inspect_target.project_wiring_source_observations(payload, info, status, snapshot_content=source, syntax_sections=crowded)
    assert payload == crowded, "The new section must drop first, never rewrite older evidence or enlarge its budget"


def _review_candidate_view(wiring_observations_inputs, name="positive"):
    source, info, status = _wiring_view_input(wiring_observations_inputs, name)
    payload = {}
    inspect_target.project_wiring_source_observations(payload, info, status, snapshot_content=source)
    return payload


def test_review_candidate_is_only_same_file_manual_context_with_shown_fact_indices(wiring_observations_inputs):
    payload = _review_candidate_view(wiring_observations_inputs)
    candidate = payload["wiring_review_candidate"]
    view = payload["wiring_source_observations"]
    assert candidate["status"] == "needs_manual_intent_review"
    assert candidate["basis"] == "same_file_source_fact_cooccurrence_only"
    assert candidate["evidence_record_indices"] == [0, 1]
    assert [view["records"][i]["kind"] for i in candidate["evidence_record_indices"]] == [
        "named_import_without_identifier_occurrence", "inline_empty_jsx_callback"]
    assert candidate["relation_status"] == "not_established" and candidate["counterevidence_status"] == "not_assessed"
    assert candidate["integration_status"] == "unknown" and candidate["mutation_proposed"] is False
    assert not any(key in candidate for key in ("confidence", "score", "delete", "automatic_fix"))
    assert "findings" not in payload


@pytest.mark.parametrize("name", ["after_wiring", "retained_export", "no_ui", "nonempty", "cap", "parse_error"])
def test_review_candidate_refuses_single_kind_partial_unavailable_and_after_wiring(wiring_observations_inputs, name):
    assert "wiring_review_candidate" not in _review_candidate_view(wiring_observations_inputs, name)


def test_review_candidate_obsolete_intent_is_not_inferred_from_a_comment(wiring_observations_inputs):
    payload = _review_candidate_view(wiring_observations_inputs, "obsolete_unknown")
    assert payload["wiring_review_candidate"]["counterevidence_status"] == "not_assessed"
    assert payload["wiring_review_candidate"]["integration_status"] == "unknown"


@pytest.mark.parametrize("damage", ["partial", "source_binding", "hidden", "single_kind", "bool_count", "zero_mutation",
    "relation", "runtime", "shown", "producer_omission", "request_policy"])
def test_review_candidate_cannot_promote_a_partial_hidden_or_foreign_view(wiring_observations_inputs, damage):
    view = deepcopy(_review_candidate_view(wiring_observations_inputs)["wiring_source_observations"])
    if damage == "partial": view["status"] = "partial"
    elif damage == "source_binding": view["source_binding"] = "unavailable"
    elif damage == "hidden": view["records"] = view["records"][:1]; view["shown"] = 1; view["omitted"] = 1
    elif damage == "single_kind": view["records"][1] = deepcopy(view["records"][0])
    elif damage == "bool_count": view["producer_omitted"] = False
    elif damage == "zero_mutation": view["mutation_proposed"] = 0
    elif damage == "relation": view["integration_status"] = "incomplete"
    elif damage == "runtime": view["runtime_execution"] = "proven"
    elif damage == "shown": view["shown"] = 1
    elif damage == "producer_omission": view["producer_omitted"] = 1
    elif damage == "request_policy": view["review_policy"] = "old"
    assert dead_code_intent_review.project_wiring_review_candidate(view) is None


@pytest.mark.parametrize("damage", ["missing", "extra", "one_kind", "duplicate_kind", "unknown_kind", "list_kind", "status", "basis", "request_missing"])
def test_review_candidate_missing_or_weakened_policy_withholds_only_label(wiring_observations_inputs, damage):
    payload = _review_candidate_view(wiring_observations_inputs)
    view = payload["wiring_source_observations"]
    contract = deepcopy(dead_code_intent_review.load_json_object_strict_cached(
        dead_code_intent_review.CONFIG_DIR/"dead_code_language_agnostic_core_contract.json", label="test"))
    policy = contract["manual_review_candidate"]
    if damage == "missing": contract.pop("manual_review_candidate")
    elif damage == "extra": policy["auto_delete"] = True
    elif damage == "one_kind": policy["required_fact_kinds"] = policy["required_fact_kinds"][:1]
    elif damage == "duplicate_kind": policy["required_fact_kinds"][1] = policy["required_fact_kinds"][0]
    elif damage == "unknown_kind": policy["required_fact_kinds"][1] = "guessed_relation"
    elif damage == "list_kind": policy["required_fact_kinds"][1] = []
    elif damage == "status": policy["status"] = "confirmed_incomplete"
    elif damage == "basis": policy["basis"] = "intended_relation"
    elif damage == "request_missing": contract.pop("intent_review_request")
    before = deepcopy(view)
    with patch.object(dead_code_intent_review, "load_json_object_strict_cached", return_value=contract):
        assert dead_code_intent_review.project_wiring_review_candidate(view) is None
    assert view == before


def test_review_candidate_policy_content_binds_evidence_order_without_mutating_facts(wiring_observations_inputs):
    payload = _review_candidate_view(wiring_observations_inputs)
    view = payload["wiring_source_observations"]
    contract = deepcopy(dead_code_intent_review.load_json_object_strict_cached(
        dead_code_intent_review.CONFIG_DIR/"dead_code_language_agnostic_core_contract.json", label="test"))
    contract["manual_review_candidate"]["required_fact_kinds"].reverse()
    with patch.object(dead_code_intent_review, "load_json_object_strict_cached", return_value=contract):
        changed = dead_code_intent_review.project_wiring_review_candidate(view)
    assert changed["evidence_record_indices"] == [1, 0]
    assert changed["policy_sha256"] != payload["wiring_review_candidate"]["policy_sha256"]
    assert view["records"][0]["local_name"] == "selected"


def test_review_candidate_drops_before_existing_facts_when_complete_packet_is_crowded(wiring_observations_inputs):
    from tools.core.agent_packet_budget import DEFAULT_AGENT_PACKET_BUDGET_TOKENS, context_budget_profile
    source, info, status = _wiring_view_input(wiring_observations_inputs)
    original = _review_candidate_view(wiring_observations_inputs)
    without_label = {key:value for key,value in original.items() if key != "wiring_review_candidate"}
    padding = DEFAULT_AGENT_PACKET_BUDGET_TOKENS * 4 - len(json.dumps(without_label)) - 48
    payload = {"padding": "x" * padding}
    inspect_target.project_wiring_source_observations(payload, info, status, snapshot_content=source)
    assert payload["wiring_source_observations"] == original["wiring_source_observations"]
    assert payload["wiring_review_request"] == original["wiring_review_request"]
    assert "wiring_review_candidate" not in payload
    assert context_budget_profile(json.dumps(payload), budget_tokens=DEFAULT_AGENT_PACKET_BUDGET_TOKENS)["status"] == "pass"


@pytest.mark.parametrize("name,has_candidate", [
    ("pending_comment", True), ("retired_comment", True), ("deliberate_noop", True),
    ("dynamic_string", True), ("foreign_module", True), ("unrelated_owner", True),
    ("consumed_elsewhere", False), ("unreachable_use", False), ("retained_export", False),
    ("after_wiring", False), ("wired_with_other_empty", False),
])
def test_wiring_contrast_intent_and_use_are_not_semantic_verdicts(wiring_observations_inputs, name, has_candidate):
    from tools.core.wiring_source_observations import BOUNDARIES
    payload = _review_candidate_view(wiring_observations_inputs, name)
    view = payload["wiring_source_observations"]
    assert view["status"] == "observed" and view["source_binding"] == "snapshot_and_live_match"
    assert all(view[k] == value for k,value in BOUNDARIES.items())
    assert ("wiring_review_candidate" in payload) is has_candidate
    assert "findings" not in payload and "safe_to_apply" not in payload
    if has_candidate:
        candidate = payload["wiring_review_candidate"]
        assert candidate["basis"] == "same_file_source_fact_cooccurrence_only"
        assert candidate["relation_status"] == "not_established"
        assert candidate["counterevidence_status"] == "not_assessed"
        assert candidate["integration_status"] == "unknown" and candidate["mutation_proposed"] is False
        assert all(type(i) is int and 0 <= i < view["shown"] for i in candidate["evidence_record_indices"])
        assert not any(k in candidate for k in ("intended_callback", "defect", "delete", "automatic_fix"))
    else:
        # No candidate is not proof that the UI is wired or that a branch runs.
        assert view["integration_status"] == "unknown" and view["runtime_execution"] == "not_established"


def test_wiring_contrast_opposite_intent_comments_do_not_change_manual_decision(wiring_observations_inputs):
    names = ("pending_comment", "retired_comment", "deliberate_noop")
    sources = [wiring_observations_inputs[name][0] for name in names]
    assert len(set(sources)) == 3
    views = [_review_candidate_view(wiring_observations_inputs, name) for name in names]
    assert all(p["wiring_review_candidate"] == views[0]["wiring_review_candidate"] for p in views)
    hashes = [wiring_observations_inputs[name][2]["source_hash"] for name in names]
    assert len(set(hashes)) == 3  # Source binding is still content-specific, not comment-blind reuse.


def test_wiring_contrast_other_use_never_certifies_an_empty_ui_callback(wiring_observations_inputs):
    from tools.core.wiring_source_observations import CALLBACK
    for name in ("consumed_elsewhere", "unreachable_use", "wired_with_other_empty", "retained_export"):
        payload = _review_candidate_view(wiring_observations_inputs, name)
        view = payload["wiring_source_observations"]
        assert [r["kind"] for r in view["records"]] == [CALLBACK]
        assert "wiring_review_candidate" not in payload
        assert view["integration_status"] == "unknown"
        assert view["runtime_execution"] == "not_established"


def test_wiring_contrast_actual_partial_observation_cannot_be_promoted(wiring_observations_inputs):
    from tools.core.wiring_source_observations import IMPORT, CALLBACK
    lane = wiring_observations_inputs["both_kinds_partial"][2]
    assert lane["status"] == "partial" and lane["omitted"] > 0
    assert {r["kind"] for r in lane["records"]} == {IMPORT, CALLBACK}
    payload = _review_candidate_view(wiring_observations_inputs, "both_kinds_partial")
    assert payload["wiring_source_observations"]["status"] == "partial"
    assert "wiring_review_candidate" not in payload


def test_wiring_contrast_actual_display_omission_cannot_pair_a_hidden_fact(wiring_observations_inputs):
    from tools.core.wiring_source_observations import IMPORT, CALLBACK
    lane = wiring_observations_inputs["display_hidden_kind"][2]
    assert lane["status"] == "observed" and {r["kind"] for r in lane["records"]} == {IMPORT, CALLBACK}
    payload = _review_candidate_view(wiring_observations_inputs, "display_hidden_kind")
    view = payload["wiring_source_observations"]
    assert view["omitted"] > 0 and view["producer_omitted"] == 0
    assert {r["kind"] for r in view["records"]} == {IMPORT}
    assert "wiring_review_candidate" not in payload
