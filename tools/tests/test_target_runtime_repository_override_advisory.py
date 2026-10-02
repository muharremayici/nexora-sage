import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.core.artifact_validator import validate_payload
from tools.core.external_target_generation import external_target_output_slug
from tools.engines import audit as audit_engine
from tools.engines.audit import (
    _failed_safeparse_raw_fallback_review_candidates,
    _repository_bulk_direct_write_review_candidates,
)
from tools.engines.generate_atlas import _file_parser_evidence


ROOT = Path(__file__).resolve().parents[2]
PREFIX = "TypeScript:RepositoryBulkDirectWrite:"
READ_PREFIX = "TypeScript:FailedSafeParseReturnsRaw:"


def _source(
    *,
    method="bulkCreate",
    body="await db.items.bulkAdd(items);",
    base="BaseRepository",
    schema="ItemSchema",
    extra="",
) -> str:
    return (
        "import { BaseRepository } from './base';\n"
        "import { db } from './db';\n"
        "import { z } from 'zod';\n"
        "import { ItemSchema } from './schema';\n"
        f"{extra}\n"
        f"export class ItemRepository extends {base}<Item> {{\n"
        f"  constructor() {{ super('items', {schema}); }}\n"
        f"  async {method}(items: Item[]): Promise<void> {{\n"
        f"    {body}\n"
        "  }\n"
        "}\n"
    )


def _analyze(source: str, target: Path) -> tuple[list[str], list[dict]]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    target.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [node, str(ROOT / "tools/engines/ast_sequencer.cjs"), str(target)],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    symbols = json.loads(result.stdout)
    meta = next(row for row in symbols if row["name"] == "__file_meta__")
    file_data = {
        "language": "typescript", "features": meta["features"],
        "parser_evidence": _file_parser_evidence(symbols, "typescript"),
        "loc": len(source.splitlines()), "hash": "bound-fixture-hash",
        "target_ref": f"MAIN::{target.name}",
    }
    return (
        [feature for feature in meta["features"] if feature.startswith(PREFIX)],
        _repository_bulk_direct_write_review_candidates("MAIN", target.name, file_data),
    )


def _read_fallback_source(*, failure="return data as T;", success="return result.data;",
                          parsed="data", guard="!result.success",
                          before_return="console.error('invalid');") -> str:
    return (
        "class Repository<T> {\n"
        "  constructor(private schema: {safeParse(value: unknown): {success: boolean; data: T}}) {}\n"
        "  validateReadData(data: unknown): T {\n"
        f"    const result = this.schema.safeParse({parsed});\n"
        f"    if ({guard}) {{\n"
        f"      {before_return}\n"
        f"      {failure}\n"
        "    }\n"
        f"    {success}\n"
        "  }\n"
        "}\n"
    )


def _analyze_read_fallback(source: str, target: Path) -> tuple[list[str], list[dict]]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    target.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [node, str(ROOT / "tools/engines/ast_sequencer.cjs"), str(target)],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    meta = next(row for row in json.loads(result.stdout) if row["name"] == "__file_meta__")
    file_data = {
        "language": "typescript", "features": meta["features"],
        "parser_evidence": _file_parser_evidence([meta], "typescript"),
        "loc": len(source.splitlines()), "hash": "bound-fixture-hash",
        "target_ref": f"MAIN::{target.name}",
    }
    return (
        [feature for feature in meta["features"] if feature.startswith(READ_PREFIX)],
        _failed_safeparse_raw_fallback_review_candidates("MAIN", target.name, file_data),
    )


@pytest.mark.parametrize("failure", ["return data as T;", "return data;"])
def test_failed_safeparse_raw_read_fallback_is_review_only(failure, tmp_path):
    source = _read_fallback_source(failure=failure)
    features, rows = _analyze_read_fallback(source, tmp_path / "read.ts")
    assert len(features) == len(rows) == 1
    candidate = rows[0]
    assert candidate["kind"] == "failed_schema_parse_returns_raw_input"
    assert candidate["subject"] == "Repository.validateReadData"
    assert candidate["guard_line"] < candidate["sink_line"]
    assert candidate["confidence"] == "needs_runtime_proof"
    assert candidate["actionability"] == "review"
    assert "legacy-data" in candidate["claim_boundary"]
    assert len(candidate["verification_questions"]) == 2
    payload = {
        "meta": {"kind": "audit_report", "version": "test"},
        "summary": {"total": 0, "by_rule": {}, "audit_scope": {},
                    "rule_taxonomy": {}, "remediation_backlog": []},
        "audit_scope": {"atlas_project_count": 1, "audited_project_count": 1,
                        "audited_projects": ["MAIN"], "violation_project_count": 0},
        "atlas_project_count": 1, "audited_project_count": 1,
        "audited_projects": ["MAIN"], "violation_project_count": 0,
        "violations": [], "report_sections": [],
        "runtime_review_candidates": rows,
    }
    assert validate_payload("audit_report", payload) == []
    assert validate_payload("audit_report", {
        **payload,
        "runtime_review_candidates": [{
            key: value for key, value in candidate.items()
            if key != "verification_questions"
        }],
    })


def test_same_named_read_methods_keep_distinct_class_identity(tmp_path):
    first = _read_fallback_source()
    source = first + first.replace("class Repository<", "class OtherRepository<")
    features, rows = _analyze_read_fallback(source, tmp_path / "two_classes.ts")
    assert len(features) == len(rows) == 2
    assert {row["subject"] for row in rows} == {
        "Repository.validateReadData",
        "OtherRepository.validateReadData",
    }


@pytest.mark.parametrize(
    ("case", "changes"),
    [
        ("throw_on_failure", {"failure": "throw new Error('invalid');"}),
        ("return_parsed", {"failure": "return result.data;"}),
        ("return_other", {"failure": "return fallback as T;"}),
        ("wrong_guard", {"guard": "result.success"}),
        ("other_result_guard", {"guard": "!other.success"}),
        ("raw_success", {"success": "return data as T;"}),
        ("different_input", {"parsed": "cleaned"}),
        ("non_log_side_effect", {"before_return": "quarantine(data);"}),
    ],
)
def test_failed_safeparse_read_fallback_rejects_lookalikes(case, changes, tmp_path):
    source = _read_fallback_source(**changes)
    features, rows = _analyze_read_fallback(source, tmp_path / f"{case}.ts")
    assert not features and not rows


def test_failed_safeparse_read_fallback_requires_bound_atlas_evidence():
    file_data = {
        "language": "typescript",
        "features": [f"{READ_PREFIX}Repository:validateReadData:5:7"],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 10, "hash": "bound-hash", "target_ref": "MAIN::src/repository.ts",
    }
    assert len(_failed_safeparse_raw_fallback_review_candidates(
        "MAIN", "src/repository.ts", file_data,
    )) == 1
    for change in (
        {"hash": ""},
        {"target_ref": "OTHER::src/repository.ts"},
        {"language": "go"},
        {"loc": 6},
        {"parser_evidence": {"status": "degraded", "parser_kind": "typescript_compiler_api"}},
        {"features": [f"{READ_PREFIX}Repository:validateReadData:7:5"]},
    ):
        assert not _failed_safeparse_raw_fallback_review_candidates(
            "MAIN", "src/repository.ts", {**file_data, **change},
        )


def test_failed_safeparse_read_fallback_survives_real_atlas_projection(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "repository.ts").write_text(_read_fallback_source(), encoding="utf-8")
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
    file_data = atlas["MAIN"]["files"]["repository.ts"]
    assert any(feature.startswith(READ_PREFIX) for feature in file_data["features"])
    rows = _failed_safeparse_raw_fallback_review_candidates(
        "MAIN", "repository.ts", file_data,
    )
    assert len(rows) == 1
    assert rows[0]["confidence"] == "needs_runtime_proof"


def test_degraded_syntax_cannot_produce_failed_safeparse_candidate(tmp_path):
    source = _read_fallback_source().replace("return result.data;", "return (result.data;")
    features, rows = _analyze_read_fallback(source, tmp_path / "degraded.ts")
    assert not features and not rows


@pytest.mark.parametrize(
    ("method", "body"),
    [
        ("bulkCreate", "await db.items.bulkAdd(items);"),
        ("bulkUpdate", "await db.items.bulkPut(items);"),
        ("bulkCreate", "await this.table.bulkAdd(items);"),
    ],
)
def test_direct_bulk_storage_is_only_a_review_candidate(method, body, tmp_path):
    features, rows = _analyze(
        _source(method=method, body=body), tmp_path / "positive.ts",
    )
    assert len(features) == len(rows) == 1
    candidate = rows[0]
    assert candidate["kind"] == "repository_bulk_method_direct_storage_write"
    assert candidate["subject"] == f"ItemRepository.{method}"
    assert candidate["guard_line"] < candidate["sink_line"]
    assert candidate["confidence"] == "needs_runtime_proof"
    assert candidate["actionability"] == "review"
    assert len(candidate["verification_questions"]) == 2
    assert "unproven" in candidate["claim_boundary"]
    payload = {
        "meta": {"kind": "audit_report", "version": "test"},
        "summary": {"total": 0, "by_rule": {}, "audit_scope": {},
                    "rule_taxonomy": {}, "remediation_backlog": []},
        "audit_scope": {"atlas_project_count": 1, "audited_project_count": 1,
                        "audited_projects": ["MAIN"], "violation_project_count": 0},
        "atlas_project_count": 1, "audited_project_count": 1,
        "audited_projects": ["MAIN"], "violation_project_count": 0,
        "violations": [], "report_sections": [],
        "runtime_review_candidates": rows,
    }
    assert validate_payload("audit_report", payload) == []
    assert validate_payload("audit_report", {
        **payload,
        "runtime_review_candidates": [{
            key: value for key, value in candidate.items()
            if key != "verification_questions"
        }],
    })


@pytest.mark.parametrize(
    ("case", "changes"),
    [
        ("base_delegation", {"body": "await super.bulkCreate(items); await db.items.bulkAdd(items);"}),
        ("each_item_validated", {"body": "items.forEach(item => this.validateForWrite(item)); await db.items.bulkAdd(items);"}),
        ("new_method", {"method": "bulkUpsert"}),
        ("other_base", {"base": "OtherBase"}),
        ("not_awaited", {"body": "void db.items.bulkAdd(items);"}),
        ("nested_callback", {"body": "await transaction(async () => { await db.items.bulkAdd(items); });"}),
        ("shadowed_db", {"body": "const db = local; await db.items.bulkAdd(items);"}),
        ("permissive_schema", {"schema": "LooseSchema", "extra": "const LooseSchema = z.any();"}),
        ("non_storage_call", {"body": "await service.bulkAdd(items);"}),
    ],
)
def test_direct_bulk_review_rejects_lookalikes(case, changes, tmp_path):
    features, rows = _analyze(_source(**changes), tmp_path / f"{case}.ts")
    assert not features and not rows


def test_direct_bulk_review_requires_bound_atlas_evidence():
    file_data = {
        "language": "typescript",
        "features": [f"{PREFIX}ItemRepository:bulkCreate:7:8"],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 8, "hash": "bound-hash", "target_ref": "MAIN::src/repository.ts",
    }
    assert len(_repository_bulk_direct_write_review_candidates(
        "MAIN", "src/repository.ts", file_data,
    )) == 1
    for change in (
        {"hash": ""},
        {"target_ref": "OTHER::src/repository.ts"},
        {"language": "go"},
        {"loc": 7},
        {"parser_evidence": {"status": "degraded", "parser_kind": "typescript_compiler_api"}},
        {"features": [f"{PREFIX}ItemRepository:bulkCreate:8:7"]},
        {"features": [f"{PREFIX}ItemRepository:bulkUpsert:7:8"]},
    ):
        assert not _repository_bulk_direct_write_review_candidates(
            "MAIN", "src/repository.ts", {**file_data, **change},
        )


def test_type_only_storage_import_is_not_runtime_write_evidence(tmp_path):
    source = _source().replace(
        "import { db } from './db';", "import type { db } from './db';",
    )
    features, rows = _analyze(source, tmp_path / "type_only.ts")
    assert not features and not rows


def test_validating_only_first_item_does_not_cover_bulk_input(tmp_path):
    features, rows = _analyze(
        _source(body="this.validateForWrite(items[0]); await db.items.bulkAdd(items);"),
        tmp_path / "one_item_only.ts",
    )
    assert len(features) == len(rows) == 1


def test_detached_async_foreach_validation_does_not_cover_bulk_input(tmp_path):
    features, rows = _analyze(
        _source(body="items.forEach(async item => this.validateForWrite(item)); await db.items.bulkAdd(items);"),
        tmp_path / "async_validation.ts",
    )
    assert len(features) == len(rows) == 1


def test_unawaited_base_call_does_not_cover_bulk_input(tmp_path):
    features, rows = _analyze(
        _source(body="void super.bulkCreate(items); await db.items.bulkAdd(items);"),
        tmp_path / "detached_base.ts",
    )
    assert len(features) == len(rows) == 1


@pytest.mark.parametrize(
    ("feature", "kind"),
    [
        (f"{PREFIX}ItemRepository:bulkCreate:7:8", "repository_bulk_method_direct_storage_write"),
        (f"{READ_PREFIX}Repository:validateReadData:7:8", "failed_schema_parse_returns_raw_input"),
    ],
)
def test_audit_public_path_routes_repository_review_without_violation(
    monkeypatch, tmp_path, feature, kind,
):
    captured = {}
    file_data = {
        "language": "typescript",
        "features": [feature],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 8, "hash": "bound-hash", "imports": [], "symbols": [],
    }
    monkeypatch.setattr(audit_engine, "resolve_atlas_data", lambda source: (source, "fixture"))
    monkeypatch.setattr(audit_engine, "resolve_runtime_projects", lambda _root: {"MAIN": tmp_path})
    monkeypatch.setattr(audit_engine, "load_effective_architecture_policy_context", lambda _raw: ({}, ""))
    monkeypatch.setattr(audit_engine, "build_effective_project_rule_taxonomy", lambda *args, **kwargs: {})
    monkeypatch.setattr(audit_engine, "filter_violations_by_project_taxonomy",
                        lambda violations, _taxonomies: (violations, {}))
    monkeypatch.setattr(audit_engine, "get_audit_report_sections", lambda: [])
    monkeypatch.setattr(audit_engine, "get_module_root_name", lambda: "src")
    monkeypatch.setattr(audit_engine, "load_scope_authority_for_consumer",
                        lambda _raw: ({}, {"evidence_status": "BOUNDED_PROJECT_SELECTION"}))
    monkeypatch.setattr(audit_engine, "bind_consumer_projects", lambda authority, **_kwargs: authority)
    monkeypatch.setattr(
        audit_engine, "_write_outputs",
        lambda violations, _sections, summary, **kwargs:
            captured.update(violations=violations, total=summary["total"],
                            candidates=kwargs["runtime_review_candidates"]),
    )
    assert audit_engine.analyze_project(
        atlas={"MAIN": {"files": {"src/repository.ts": file_data}}},
    ) is True
    assert captured["total"] == 0
    assert len(captured["candidates"]) == 1
    assert captured["candidates"][0]["kind"] == kind
    assert all(not rows for rows in captured["violations"].values())
