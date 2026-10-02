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
from tools.engines.audit import _repository_direct_read_return_review_candidates
from tools.engines.generate_atlas import (
    AST_CONTRACT_VERSION, _file_parser_evidence, file_contract_is_current,
)


ROOT = Path(__file__).resolve().parents[2]
PREFIX = "TypeScript:RepositoryDirectReadReturn:"
INLINE_PREFIX = "TypeScript:RepositoryInlineReadReturn:"
COLLECTION_PREFIX = "TypeScript:RepositoryCollectionReadReturn:"


def _source(*, method="getById", parent="BaseRepository",
            db_import="import { db } from './db';",
            read="const record = await db.items.get(id) as Item | undefined;",
            returned="if (record) return record;",
            before_return="") -> str:
    return (
        "import { BaseRepository } from './base';\n"
        f"{db_import}\n"
        f"class ItemRepository extends {parent}<Item> {{\n"
        f"  async {method}(id: string): Promise<Item | undefined> {{\n"
        f"    {read}\n"
        f"    {before_return}\n"
        f"    {returned}\n"
        "    return undefined;\n"
        "  }\n"
        "}\n"
    )


def _analyze(source: str, path: Path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    path.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [node, str(ROOT / "tools/engines/ast_sequencer.cjs"), str(path)],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    symbols = json.loads(result.stdout)
    meta = next(row for row in symbols if row["name"] == "__file_meta__")
    features = [value for value in meta["features"] if value.startswith((PREFIX, INLINE_PREFIX, COLLECTION_PREFIX))]
    file_data = {
        "language": "typescript", "features": meta["features"],
        "parser_evidence": _file_parser_evidence(symbols, "typescript"),
        "loc": len(source.splitlines()), "hash": "fixture-source-hash",
        "target_ref": f"MAIN::{path.name}",
    }
    return features, _repository_direct_read_return_review_candidates(
        "MAIN", path.name, file_data,
    )


@pytest.mark.parametrize(
    ("db_import", "read"),
    [
        ("import { db } from './db';", "const record = await db.items.get(id) as Item | undefined;"),
        ("import { db as store } from './database/client';", "const record = await store.items.get(id);"),
        ("import { db } from './db';", "const record = await this.table.get(id);"),
    ],
)
def test_direct_repository_read_is_review_only(db_import, read, tmp_path):
    features, candidates = _analyze(
        _source(db_import=db_import, read=read), tmp_path / "positive.ts",
    )
    assert len(features) == len(candidates) == 1
    candidate = candidates[0]
    assert candidate["kind"] == "repository_direct_read_return_without_local_validation"
    assert candidate["subject"] == "ItemRepository.getById"
    assert candidate["guard_line"] < candidate["sink_line"]
    assert candidate["confidence"] == "needs_runtime_proof"
    assert candidate["actionability"] == "review"
    assert "base read path may itself return raw" in candidate["claim_boundary"]
    assert "compare with the base read path" in candidate["verification_questions"][0]
    assert len(candidate["verification_questions"]) == 3
    payload = {
        "meta": {"kind": "audit_report", "version": "test"},
        "summary": {"total": 0, "by_rule": {}, "audit_scope": {},
                    "rule_taxonomy": {}, "remediation_backlog": []},
        "audit_scope": {"atlas_project_count": 1, "audited_project_count": 1,
                        "audited_projects": ["MAIN"], "violation_project_count": 0},
        "atlas_project_count": 1, "audited_project_count": 1,
        "audited_projects": ["MAIN"], "violation_project_count": 0,
        "violations": [], "report_sections": [],
        "runtime_review_candidates": candidates,
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
    ("db_import", "expression"),
    [
        ("import { db } from './db';", "await db.items.get(id) as Item | undefined"),
        ("import { db as store } from './database/client';", "(await store.items.get(id))"),
        ("import { db } from './db';", "await this.table.get(id)"),
        ("import { db } from './db';", "(\n      await db.items.get(id)\n    ) as Item | undefined"),
    ],
)
def test_inline_repository_read_is_review_only(db_import, expression, tmp_path):
    features, candidates = _analyze(
        _source(db_import=db_import, read="", returned=f"return {expression};"),
        tmp_path / "inline.ts",
    )
    assert len(features) == len(candidates) == 1
    assert features[0].startswith(INLINE_PREFIX)
    candidate = candidates[0]
    assert candidate["kind"] == "repository_direct_read_return_without_local_validation"
    assert candidate["return_form"] == "inline_await"
    assert candidate["subject"] == "ItemRepository.getById"
    assert candidate["guard_line"] < candidate["sink_line"]
    assert candidate["confidence"] == "needs_runtime_proof"
    assert candidate["actionability"] == "review"
    assert "method declaration" in candidate["claim_boundary"]
    assert "storage record type" in candidate["verification_questions"][2]


@pytest.mark.parametrize(
    ("case", "changes"),
    [
        ("other_base", {"parent": "OtherBase"}),
        ("other_method", {"method": "getAll"}),
        ("api_source", {"db_import": "import { db } from './api';"}),
        ("type_only", {"db_import": "import type { db } from './db';"}),
        ("shadowed_db", {"read": "const db = local;"}),
        ("not_awaited", {"returned": "return db.items.get(id);"}),
        ("optional_call", {"returned": "return await db.items.get?.(id);"}),
        ("optional_receiver", {"returned": "return await db?.items.get(id);"}),
        ("validated", {"before_return": "this.validateReadData(value);"}),
        ("base_delegation", {"before_return": "await super.getById(id);"}),
        ("parsed", {"returned": "return this.schema.parse(await db.items.get(id));"}),
        ("nested", {"returned": "return async () => await db.items.get(id);"}),
        ("conditional", {"returned": "return id ? await db.items.get(id) : undefined;"}),
        ("non_get", {"returned": "return await db.items.find(id);"}),
    ],
)
def test_inline_read_rejects_lookalikes(case, changes, tmp_path):
    source = _source(**{
        "read": "", "returned": "return await db.items.get(id) as Item | undefined;",
        **changes,
    })
    features, candidates = _analyze(source, tmp_path / f"inline_{case}.ts")
    assert not features and not candidates


def test_inline_read_preserves_degraded_and_sync_boundaries(tmp_path):
    for source in (
        _source(read="", returned="return await db.items.get(id);").replace(
            "return undefined;", "return (undefined;"
        ),
        _source(read="", returned="return await db.items.get(id);").replace("async getById", "getById"),
    ):
        features, candidates = _analyze(source, tmp_path / "unsupported.ts")
        assert not features and not candidates


@pytest.mark.parametrize(
    ("case", "changes"),
    [
        ("other_method", {"method": "getAll"}),
        ("other_base", {"parent": "OtherBase"}),
        ("api_get", {"db_import": "import { db } from './api';"}),
        ("lookalike_module", {"db_import": "import { db } from './notdexie';"}),
        ("type_only_db", {"db_import": "import type { db } from './db';"}),
        ("non_get", {"read": "const record = await db.items.find(id);"}),
        ("not_awaited", {"read": "const record = db.items.get(id);"}),
        ("other_return", {"returned": "if (record) return fallback;"}),
        ("validated", {"before_return": "this.validateReadData(record);"}),
        ("parsed", {"before_return": "this.schema.parse(record);"}),
        ("base_delegation", {"before_return": "await super.getById(id);"}),
        ("shadowed_import", {"read": "const db = local; const record = await db.items.get(id);"}),
        ("nested_return", {"returned": "const callback = () => record; return callback();"}),
    ],
)
def test_direct_read_rejects_lookalikes(case, changes, tmp_path):
    features, candidates = _analyze(_source(**changes), tmp_path / f"{case}.ts")
    assert not features and not candidates


def test_class_identity_and_bound_atlas_evidence(tmp_path):
    source = _source() + _source().replace("class ItemRepository ", "class OtherRepository ")
    features, candidates = _analyze(source, tmp_path / "two_classes.ts")
    assert len(features) == len(candidates) == 2
    assert {row["subject"] for row in candidates} == {
        "ItemRepository.getById", "OtherRepository.getById",
    }
    file_data = {
        "language": "typescript",
        "features": [f"{PREFIX}ItemRepository:getById:5:7"],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 10, "hash": "bound", "target_ref": "MAIN::src/repository.ts",
    }
    for prefix in (PREFIX, INLINE_PREFIX):
        bound = {**file_data, "features": [f"{prefix}ItemRepository:getById:5:7"]}
        assert len(_repository_direct_read_return_review_candidates(
            "MAIN", "src/repository.ts", bound,
        )) == 1
        for change in (
            {"hash": ""}, {"language": "go"}, {"loc": 6},
            {"target_ref": "OTHER::src/repository.ts"},
            {"parser_evidence": {"status": "degraded", "parser_kind": "typescript_compiler_api"}},
            {"features": [f"{prefix}ItemRepository:getById:7:5"]},
            {"features": [f"{prefix}ItemRepository:getById:5:5"]},
        ):
            assert not _repository_direct_read_return_review_candidates(
                "MAIN", "src/repository.ts", {**bound, **change},
            )


def test_degraded_parser_does_not_emit_direct_read(tmp_path):
    source = _source().replace("return undefined;", "return (undefined;")
    features, candidates = _analyze(source, tmp_path / "degraded.ts")
    assert not features and not candidates


def test_direct_read_survives_tiny_external_target_atlas(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    inline = _source(read="", returned="return await db.items.get(id) as Item | undefined;")
    inline = inline[inline.index("class ItemRepository"):].replace(
        "class ItemRepository", "class InlineRepository"
    )
    collections = []
    for name, body in (
        ("CollectionRepository", "const records = await db.items.toArray();\n    return records as Item[];"),
        ("InlineCollectionRepository", "return await db.items.toArray() as Item[];"),
        ("FilteredCollectionRepository", "const records = await db.items.toArray();\n    return records.filter(item => !item.deletedAt);"),
    ):
        collection = _collection_source(body)
        collections.append(collection[collection.index("class ItemRepository"):].replace(
            "class ItemRepository", f"class {name}",
        ))
    (target / "repository.ts").write_text(
        "\n".join([_source(), inline, *collections]), encoding="utf-8",
    )
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
        cwd=ROOT, env=env, capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    atlas = json.loads((
        ROOT / "output" / "external_targets" / external_target_output_slug(str(target))
        / ".raw" / "atlas.json"
    ).read_text(encoding="utf-8"))
    file_data = atlas["MAIN"]["files"]["repository.ts"]
    assert any(value.startswith(PREFIX) for value in file_data["features"])
    assert any(value.startswith(INLINE_PREFIX) for value in file_data["features"])
    assert sum(value.startswith(COLLECTION_PREFIX) for value in file_data["features"]) == 3
    assert file_data["ast_contract_version"] == AST_CONTRACT_VERSION
    assert file_contract_is_current(file_data)
    assert not file_contract_is_current({
        **file_data, "ast_contract_version": "v18.28-repository-inline-read-return-review",
    })
    candidates = _repository_direct_read_return_review_candidates(
        "MAIN", "repository.ts", file_data,
    )
    assert len(candidates) == 5
    assert {row["return_form"] for row in candidates} == {"const_binding", "inline_await", "filtered_binding"}
    assert all(row["source_hash"] == file_data["hash"] for row in candidates)
    payload = {
        "meta": {"kind": "audit_report", "version": "test"},
        "summary": {"total": 0, "by_rule": {}, "audit_scope": {},
                    "rule_taxonomy": {}, "remediation_backlog": []},
        "audit_scope": {"atlas_project_count": 1, "audited_project_count": 1,
                        "audited_projects": ["MAIN"], "violation_project_count": 0},
        "atlas_project_count": 1, "audited_project_count": 1,
        "audited_projects": ["MAIN"], "violation_project_count": 0,
        "violations": [], "report_sections": [], "runtime_review_candidates": candidates,
    }
    assert validate_payload("audit_report", payload) == []
    single_candidate = next(
        row for row in candidates
        if row["kind"] == "repository_direct_read_return_without_local_validation"
    )
    assert validate_payload("audit_report", {
        **payload, "runtime_review_candidates": [{**single_candidate, "return_form": "filtered_binding"}],
    })
    collection_candidate = next(row for row in candidates if row["return_form"] == "filtered_binding")
    for missing in ("subject", "return_form", "verification_questions"):
        assert validate_payload("audit_report", {
            **payload, "runtime_review_candidates": [{
                key: value for key, value in collection_candidate.items() if key != missing
            }],
        })
    payload["runtime_review_candidates"] = [{**candidates[1], "return_form": "runtime_confirmed"}]
    assert validate_payload("audit_report", payload)


@pytest.mark.parametrize("prefix", [PREFIX, INLINE_PREFIX, COLLECTION_PREFIX])
def test_audit_public_path_does_not_count_candidate_as_violation(monkeypatch, tmp_path, prefix):
    captured = {}
    file_data = {
        "language": "typescript", "features": [
            f"{prefix}ItemRepository:getAll:const_binding:5:7" if prefix == COLLECTION_PREFIX
            else f"{prefix}ItemRepository:getById:5:7"
        ],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 10, "hash": "bound", "imports": [], "symbols": [],
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
    assert captured["candidates"][0]["confidence"] == "needs_runtime_proof"
    assert captured["candidates"][0]["return_form"] == (
        "inline_await" if prefix == INLINE_PREFIX else "const_binding"
    )
    assert all(not rows for rows in captured["violations"].values())


def _collection_source(body, *, method="getAll", parent="BaseRepository",
                       db_import="import { db } from './db';"):
    return (
        "import { BaseRepository } from './base';\n"
        f"{db_import}\n"
        f"class ItemRepository extends {parent}<Item> {{\n"
        f"  async {method}(ownerId: string): Promise<Item[]> {{\n"
        f"    {body}\n"
        "  }\n"
        "}\n"
    )


@pytest.mark.parametrize(
    ("body", "form", "method", "db_import"),
    [
        ("const records = await db.items.toArray();\n    return records as Item[];",
         "const_binding", "getAll", "import { db } from './db';"),
        ("const records = await store.items.where('ownerId').equals(ownerId).toArray();\n    return records;",
         "const_binding", "getAllByOwnerId", "import { db as store } from './database/client';"),
        ("const records = await this.table.toArray();\n    return records;",
         "const_binding", "getAll", "import { db } from './db';"),
        ("return (await db.items.toArray()) as Item[];",
         "inline_await", "getAll", "import { db } from './db';"),
        ("return await this.table.toArray();",
         "inline_await", "getAllByOwnerId", "import { db } from './db';"),
        ("const records = await db.items.filter(item => !item.deletedAt).toArray();\n    return records;",
         "const_binding", "getAll", "import { db } from './db';"),
        ("const records = await db.items.toArray();\n    return records.filter(item => !item.deletedAt);",
         "filtered_binding", "getAllByOwnerId", "import { db } from './db';"),
        ("const records = await db.items.where('ownerId').equals(ownerId).toArray();\n    return records.filter(item => item.active && item.version > 0) as Item[];",
         "filtered_binding", "getAllByOwnerId", "import { db } from './db';"),
    ],
)
def test_collection_read_return_is_review_only(body, form, method, db_import, tmp_path):
    features, candidates = _analyze(
        _collection_source(body, method=method, db_import=db_import), tmp_path / "collection.ts",
    )
    assert len(features) == len(candidates) == 1
    candidate = candidates[0]
    assert candidate["kind"] == "repository_collection_read_return_without_local_validation"
    assert candidate["subject"] == f"ItemRepository.{method}"
    assert candidate["return_form"] == form
    assert candidate["confidence"] == "needs_runtime_proof"
    assert candidate["actionability"] == "review"
    assert candidate["guard_line"] < candidate["sink_line"]
    assert "intentional legacy" in candidate["claim_boundary"]
    assert len(candidate["verification_questions"]) == 3
    if form == "inline_await":
        assert "method declaration" in candidate["claim_boundary"]


@pytest.mark.parametrize(
    ("case", "body", "changes"),
    [
        ("other_method", "return await db.items.toArray();", {"method": "listItems"}),
        ("other_base", "return await db.items.toArray();", {"parent": "OtherBase"}),
        ("api", "return await db.items.toArray();", {"db_import": "import { db } from './api';"}),
        ("type_only_db", "return await db.items.toArray();", {"db_import": "import type { db } from './db';"}),
        ("type_only_base", "return await db.items.toArray();", {}),
        ("shadowed_import", "const records = await db.items.toArray();\n    return records;", {}),
        ("nonawait", "return db.items.toArray();", {}),
        ("sync", "return await db.items.toArray();", {}),
        ("parse_degraded", "return await db.items.toArray(;", {}),
        ("optional_call", "return await db.items.toArray?.();", {}),
        ("optional_receiver", "return await db?.items.toArray();", {}),
        ("optional_chain", "return await db.items.where?.('ownerId').equals(ownerId).toArray();", {}),
        ("nonempty_toarray", "return await db.items.toArray(callback);", {}),
        ("noncollection_get", "const records = await db.items.get(ownerId);\n    return records;", {}),
        ("validated", "const records = await db.items.toArray();\n    return records.map(item => this.validateReadData(item));", {}),
        ("schema_transform", "const records = await db.items.toArray();\n    return this.schema.parse(records);", {}),
        ("delegated", "return await super.getAll();", {}),
        ("prevalidation", "const records = await db.items.toArray();\n    records.forEach(item => this.validateReadData(item));\n    return records;", {}),
        ("mutated", "const records = await db.items.toArray();\n    records.push(extra);\n    return records;", {}),
        ("nested", "const records = await db.items.toArray();\n    return (() => records)();", {}),
        ("other_binding", "const records = await db.items.toArray();\n    return fallback;", {}),
        ("mapped_query", "return await db.items.map(item => item).toArray();", {}),
        ("callback_filter", "return await db.items.filter(validateRecord).toArray();", {}),
        ("validation_filter", "const records = await db.items.toArray();\n    return records.filter(item => this.validateReadData(item));", {}),
        ("mutating_filter", "const records = await db.items.toArray();\n    return records.filter(item => item.active = true);", {}),
        ("async_filter", "const records = await db.items.toArray();\n    return records.filter(async item => item.active);", {}),
        ("block_filter", "const records = await db.items.toArray();\n    return records.filter(item => { return item.active; });", {}),
    ],
)
def test_collection_read_rejects_unproved_or_transformed_paths(case, body, changes, tmp_path):
    source = _collection_source(body, **changes)
    if case == "type_only_base":
        source = source.replace("import { BaseRepository }", "import type { BaseRepository }")
    elif case == "shadowed_import":
        source = source.replace("(ownerId: string)", "(ownerId: string, db: unknown)")
    elif case == "sync":
        source = source.replace("async getAll", "getAll")
    features, candidates = _analyze(source, tmp_path / f"{case}.ts")
    assert not features and not candidates


@pytest.mark.parametrize("form", ["const_binding", "inline_await", "filtered_binding"])
def test_collection_read_requires_bound_atlas_metadata(form):
    feature = f"{COLLECTION_PREFIX}ItemRepository:getAllByOwnerId:{form}:5:7"
    data = {
        "language": "typescript", "features": [feature],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 10, "hash": "bound", "target_ref": "MAIN::src/repository.ts",
    }
    assert len(_repository_direct_read_return_review_candidates("MAIN", "src/repository.ts", data)) == 1
    for change in (
        {"hash": ""}, {"language": "go"}, {"loc": 6},
        {"target_ref": "OTHER::src/repository.ts"},
        {"parser_evidence": {"status": "degraded", "parser_kind": "typescript_compiler_api"}},
        {"features": [feature.replace(":5:7", ":7:5")]},
        {"features": [feature.replace(":5:7", ":5:5")]},
        {"features": [feature.replace(form, "confirmed_runtime")]},
        {"features": [feature.replace("getAllByOwnerId", "listItems")]},
    ):
        assert not _repository_direct_read_return_review_candidates(
            "MAIN", "src/repository.ts", {**data, **change},
        )
