from pathlib import Path

import tools.core.config as runtime_config
from tools.validate_react_support import (
    _not_applicable_payload,
    _runtime_atlas_payload,
    _runtime_project_roots,
)


def test_non_react_project_dna_produces_schema_complete_not_applicable_matrix():
    payload = _not_applicable_payload(
        {
            "projects": [
                {
                    "project": "MAIN",
                    "display_name": "Python target",
                    "roots": ["."],
                    "source_file_count": 3,
                    "frameworks": [],
                }
            ]
        }
    )

    assert payload["applicability"]["status"] == "NOT_APPLICABLE"
    assert payload["summary"] == {"repo_present": 0, "detected": 0, "partial": 0, "missing": 0}
    assert payload["cache"]["status"] == "not_applicable"
    assert payload["by_project"]["MAIN"]["source_file_count"] == 3
    assert payload["capabilities"]
    assert {row["support"] for row in payload["capabilities"]} == {"not_applicable"}


def test_runtime_project_filter_keeps_registered_variants_out_of_evaluation(monkeypatch):
    roots = {
        "MAIN": Path("src"),
        "VARIANT_ONE": Path("Variations/One"),
        "VARIANT_TWO": Path("Variations/Two"),
    }
    monkeypatch.setattr(runtime_config, "PROJECT_FILTER", ["MAIN"])

    evaluated, requested = _runtime_project_roots(roots)

    assert evaluated == {"MAIN": Path("src")}
    assert requested == ["MAIN"]


def test_runtime_project_filter_keeps_variants_out_of_workspace_claim_input():
    atlas = {
        "MAIN": {"files": {"host.tsx": {}}},
        "VARIANT_ONE": {"files": {"variant.tsx": {}}},
        "symbols": [],
    }

    scoped = _runtime_atlas_payload(atlas, ["MAIN"])

    assert scoped == {"MAIN": {"files": {"host.tsx": {}}}}
