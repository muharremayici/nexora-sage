import json
import os

import pytest

from tools.core.config import CODE_MAPS_DIR


@pytest.mark.parametrize("case", ["none", "missing", "ambiguous_files", "multiple_targets", "duplicate_scope", "duplicate_owner", "not_selected", "outside", "foreign_caller", "same_project", "relative", "unknown_alias", "prefix_collision", "traversal", "query", "unknown_language", "longer_alias_missing", "longer_alias_unsupported", "longer_alias_empty"])
def test_workspace_alias_source_candidate_is_unique_confined_and_optional(tmp_path, monkeypatch, case):
    from copy import deepcopy
    from tools.core import path_engine
    root = tmp_path / "workspace"
    app, lib = root / "apps/app", root / "packages/lib"
    app.mkdir(parents=True)
    lib.mkdir(parents=True)
    (lib / "factory.ts").write_text("export const make = () => 1;", encoding="utf-8")
    projects = {"APP": app, "LIB": lib}
    scopes = [{"scope_root": "apps/app", "base_url": ".", "path_aliases": {"@lib/*": ["../../packages/lib/*"]}}]
    source, language, current = "@lib/factory", "typescript", str(app)
    if case == "missing": (lib / "factory.ts").unlink()
    elif case == "ambiguous_files": (lib / "factory.tsx").write_text("export const other = 1;")
    elif case == "multiple_targets": scopes[0]["path_aliases"]["@lib/*"].append("../../other/*")
    elif case == "duplicate_scope": scopes.append(deepcopy(scopes[0]))
    elif case == "duplicate_owner": projects["OTHER"] = lib
    elif case == "not_selected": projects.pop("LIB")
    elif case == "outside":
        external = tmp_path / "external"
        external.mkdir()
        (external / "factory.ts").write_text("export const other = 1;")
        scopes[0]["path_aliases"]["@lib/*"] = ["../../../external/*"]
        projects["EXTERNAL"] = external
    elif case == "foreign_caller": current = str(tmp_path)
    elif case == "same_project": projects = {"APP": root}
    elif case == "relative": source = "../../packages/lib/factory"
    elif case == "unknown_alias": source = "@unknown/factory"
    elif case == "prefix_collision": source = "@library/factory"
    elif case == "traversal": source = "@lib/../lib/factory"
    elif case == "query": source += "?raw"
    elif case == "unknown_language": language = "python"
    elif case == "longer_alias_missing": scopes[0]["path_aliases"]["@lib/factory"] = ["../../absent"]
    elif case == "longer_alias_unsupported": scopes[0]["path_aliases"]["@lib/factory"] = ["../../one", "../../two"]
    elif case == "longer_alias_empty": scopes[0]["path_aliases"]["@lib/factory"] = []
    monkeypatch.setitem(path_engine.ENVIRONMENT, "scoped_path_aliases", scopes)
    monkeypatch.setattr(os, "walk", lambda *a, **k: pytest.fail("No second workspace/package walk"))
    candidate = path_engine.workspace_import_source_candidate(source, current, str(root), "APP", projects, language=language)
    assert candidate == ({"version": "v1-declared-workspace-alias-source", "source_project": "APP",
                          "target_ref": "LIB::factory.ts", "workspace_rel": "packages/lib/factory.ts"} if case == "none" else None)


@pytest.mark.parametrize("damage", ["none", "version", "extra", "missing"])
def test_workspace_import_source_schema_does_not_certify_legacy_or_promoted_records(damage):
    from tools.core.artifact_validator import _validate
    schema = json.loads((CODE_MAPS_DIR / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    row = {"version":"v1-declared-workspace-alias-source", "source_project":"APP", "target_ref":"LIB::factory.ts", "workspace_rel":"packages/lib/factory.ts"}
    if damage == "version": row["version"] = "future"
    elif damage == "extra": row["runtime_proof"] = True
    elif damage == "missing": row.pop("source_project")
    errors = []
    _validate(row, schema["$defs"]["workspace_import_source_candidate"], schema, ["candidate"], errors)
    assert bool(errors) == (damage != "none")
