import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.core.artifact_validator import ensure_against_schema
from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, DYNAMIC_CONFIG, ENVIRONMENT
from tools.core.path_engine import get_alias_map, reset_path_resolution_caches, resolve_project_import
from tools.core.polyglot_imports import extract_imports
from tools.engines import generate_atlas as generate_atlas_module
from tools.engines.ast_sequencer_cs import sequence_cs_file, sequence_cs_file_with_evidence
from tools.engines.ast_sequencer_go import sequence_go_file, sequence_go_file_with_evidence
from tools.engines.ast_sequencer_java import sequence_java_file, sequence_java_file_with_evidence
from tools.engines.ast_sequencer_python import sequence_python_file, sequence_python_file_with_evidence
from tools.engines.generate_atlas import (
    _build_project_symbol_occurrences,
    _decode_node_batch_response,
    _normalize_polyglot_symbol,
    _normalize_polyglot_symbols,
    _raw_import_sources_not_in_records,
    file_contract_is_current,
    previous_atlas_required_for_generation,
)


def test_node_batch_response_rejects_wrong_identity_partial_and_malformed_payloads():
    expected = {"C:/repo/a.ts", "C:/repo/b.ts"}
    wrong_identity = json.dumps({
        "batchMeta": {
            "requestId": "wrong",
            "filesRequested": 2,
            "filesReported": 2,
        },
        "results": {path: [] for path in expected},
    })
    partial = json.dumps({
        "batchMeta": {
            "requestId": "expected",
            "filesRequested": 2,
            "filesReported": 2,
        },
        "results": {"C:/repo/a.ts": []},
    })

    self_results, _, wrong_error = _decode_node_batch_response(
        wrong_identity,
        request_id="expected",
        expected_paths=expected,
    )
    partial_results, _, partial_error = _decode_node_batch_response(
        partial,
        request_id="expected",
        expected_paths=expected,
    )
    malformed_results, _, malformed_error = _decode_node_batch_response(
        "{",
        request_id="expected",
        expected_paths=expected,
    )

    assert self_results == {}
    assert wrong_error == "request_identity_mismatch"
    assert partial_results == {}
    assert partial_error == "response_file_set_mismatch"
    assert malformed_results == {}
    assert malformed_error == "malformed_json"


def _sequence_node_file(path: Path) -> list[dict]:
    result = subprocess.run(
        ["node", str(CODE_MAPS_DIR / "tools" / "engines" / "ast_sequencer.cjs"), str(path)],
        cwd=str(CODE_MAPS_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_polyglot_import_extractor_covers_python_java_go_csharp():
    assert extract_imports("from pkg.service import Worker\nimport pkg.tools\n", "python") == [
        "pkg/service",
        "pkg/tools",
    ]
    assert extract_imports("import com.acme.Service;\nimport static com.acme.Util.*;\n", "java") == [
        "com/acme/Service",
        "com/acme/Util",
    ]
    assert extract_imports('import (\n  "net/http"\n  "book/internal"\n)\n', "go") == [
        "net/http",
        "book/internal",
    ]
    assert extract_imports("using System.Text;\nusing My.App.Core;\n", "csharp") == [
        "System/Text",
        "My/App/Core",
    ]


def test_typescript_type_only_imports_do_not_create_runtime_edges(tmp_path):
    source = """
        import type { PanelState } from './ModuleLayout';
        import { type ConsistencyCheckResult } from '@/writing/useWritingIntegration';
        export type { PublicShape } from './public-shape';
        export { type InternalShape } from './internal-shape';
        import { RuntimeThing, type RuntimeOptions } from './runtime-thing';
        export { RuntimeExport, type RuntimeExportShape } from './runtime-export';
    """
    target = tmp_path / "type-imports.ts"
    target.write_text(source, encoding="utf-8")
    parser_entries = _sequence_node_file(target)
    assert extract_imports(source, "typescript", parser_entries=parser_entries) == [
        "./runtime-thing",
        "./runtime-export",
    ]

def test_unrecorded_typescript_import_sources_are_preserved_for_evidence():
    raw_imports = ["@/platform/ai/types/ai", "./runtime-thing"]
    rich_records = [
        {
            "source": "runtime-thing.ts",
            "raw_source": "./runtime-thing",
            "name": "RuntimeThing",
            "kind": "named",
        }
    ]

    assert _raw_import_sources_not_in_records(raw_imports, rich_records) == [
        "@/platform/ai/types/ai",
    ]


def test_polyglot_import_resolution_finds_language_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pkg").mkdir()
        (root / "pkg" / "service.py").write_text("def run(): pass\n", encoding="utf-8")
        assert resolve_project_import("pkg/service", str(root), str(root), str(root), language="python") == "pkg/service.py"

        java_dir = root / "com" / "acme"
        java_dir.mkdir(parents=True)
        (java_dir / "Service.java").write_text("class Service {}\n", encoding="utf-8")
        assert resolve_project_import("com/acme/Service", str(root), str(root), str(root), language="java") == "com/acme/Service.java"

        go_dir = root / "book" / "internal"
        go_dir.mkdir(parents=True)
        (go_dir / "index.go").write_text("package internal\n", encoding="utf-8")
        resolved = resolve_project_import("book/internal/index", str(root), str(root), str(root), language="go")
        assert resolved == "book/internal/index.go"

        cs_dir = root / "My" / "App"
        cs_dir.mkdir(parents=True)
        (cs_dir / "Core.cs").write_text("namespace My.App;\n", encoding="utf-8")
        assert resolve_project_import("My/App/Core", str(root), str(root), str(root), language="csharp") == "My/App/Core.cs"


def test_typescript_workspace_alias_resolution_uses_nearest_scope_and_file_identity():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lib_source = root / "lib" / "src"
        website_source = root / "website" / "build-app"
        (lib_source / "utils").mkdir(parents=True)
        (lib_source / "components" / "Card").mkdir(parents=True)
        website_source.mkdir(parents=True)
        (lib_source / "utils" / "index.ts").write_text("export const value = 1\n", encoding="utf-8")
        scopes = [
            {
                "config_file": "lib/tsconfig.json",
                "scope_root": "lib",
                "base_url": ".",
                "path_aliases": {"@/*": ["./src/*"]},
            },
            {
                "config_file": "website/tsconfig.json",
                "scope_root": "website",
                "base_url": ".",
                "path_aliases": {"@/*": ["../lib/src/*"]},
            },
        ]
        with patch.dict(ENVIRONMENT, {"path_aliases": {}, "scoped_path_aliases": scopes}, clear=False):
            assert resolve_project_import(
                "@/utils", str(lib_source / "components" / "Card"), str(root), str(root)
            ) == "lib/src/utils/index.ts"
            assert resolve_project_import(
                "@/utils", str(website_source), str(root), str(root)
            ) == "lib/src/utils/index.ts"


def test_typescript_workspace_package_subpath_resolves_to_source_entry():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        package_root = root / "packages" / "utilities"
        consumer_root = root / "packages" / "app" / "src"
        (package_root / "src").mkdir(parents=True)
        consumer_root.mkdir(parents=True)
        (package_root / "package.json").write_text(
            json.dumps(
                {
                    "name": "@example/utilities",
                    "exports": {
                        "./internal": {
                            "types": "./dist/internal.d.ts",
                            "import": "./dist/internal.js",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (package_root / "src" / "internal.ts").write_text(
            "export const value = 1\n",
            encoding="utf-8",
        )

        assert resolve_project_import(
            "@example/utilities/internal",
            str(consumer_root),
            str(root),
            str(root),
        ) == "packages/utilities/src/internal.ts"

        assert resolve_project_import(
            "@example/utilities/private",
            str(consumer_root),
            str(root),
            str(root),
        ) == "@example/utilities/private"


def test_workspace_package_resolution_cache_refreshes_between_atlas_materializations():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        package_root = root / "packages" / "utilities"
        consumer_root = root / "packages" / "app" / "src"
        (package_root / "src").mkdir(parents=True)
        consumer_root.mkdir(parents=True)
        manifest_path = package_root / "package.json"
        manifest_path.write_text(
            json.dumps({"name": "@example/utilities", "exports": {".": "./dist/index.js"}}),
            encoding="utf-8",
        )
        (package_root / "src" / "extra.ts").write_text("export const extra = 1\n", encoding="utf-8")

        assert resolve_project_import(
            "@example/utilities/extra", str(consumer_root), str(root), str(root)
        ) == "@example/utilities/extra"
        manifest_path.write_text(
            json.dumps(
                {
                    "name": "@example/utilities",
                    "exports": {
                        ".": "./dist/index.js",
                        "./extra": "./dist/extra.js",
                    },
                }
            ),
            encoding="utf-8",
        )
        reset_path_resolution_caches()

        assert resolve_project_import(
            "@example/utilities/extra", str(consumer_root), str(root), str(root)
        ) == "packages/utilities/src/extra.ts"


def test_external_target_without_alias_evidence_does_not_inherit_default_alias():
    with patch.dict(ENVIRONMENT, {"path_aliases": {}, "scoped_path_aliases": []}, clear=False):
        with patch.dict(DYNAMIC_CONFIG, {"_target_root_override": {"enabled": True}}, clear=False):
            assert get_alias_map() == {}


def test_generate_atlas_source_contains_polyglot_normalization_contract():
    atlas_source = Path(__file__).parents[1] / "engines" / "generate_atlas.py"
    text = atlas_source.read_text(encoding="utf-8")
    assert "_normalize_polyglot_symbol" in text
    assert "extract_polyglot_imports" in text
    assert "polyglot-v1" in text


def test_polyglot_normalization_promotes_start_end_into_canonical_line_span():
    normalized = _normalize_polyglot_symbol(
        {
            "name": "build_context",
            "type": "Function",
            "start": 14,
            "end": 27,
            "signature": "def build_context",
        },
        "def build_context():\n    return {}\n",
        language="python",
    )

    assert normalized["line"] == 14
    assert normalized["end_line"] == 27
    assert normalized["source_lines"] == "L14-L27"


def test_typescript_offsets_are_normalized_from_utf16_units_to_utf8_bytes():
    content = "const title = 'Turkce: \u00e7';\nexport default App;\n"
    signature = "export default App;"
    start_chars = content.index(signature)
    normalized = _normalize_polyglot_symbol(
        {
            "name": "default_export",
            "type": "DefaultExport",
            "start": start_chars,
            "end": start_chars + len(signature),
            "signature": signature,
            "parserKind": "typescript_compiler_api",
        },
        content,
        language="typescript",
    )

    expected_start = len(content[:start_chars].encode("utf-8"))
    expected_end = expected_start + len(signature.encode("utf-8"))
    assert normalized["start"] == expected_start
    assert normalized["end"] == expected_end
    assert content.encode("utf-8")[expected_start:expected_end].decode("utf-8") == signature


def test_typescript_offset_normalization_handles_astral_unicode_and_crlf():
    content = "const marker = '\U0001f642';\r\nexport default App;\r\n"
    signature = "export default App;"
    start_chars = content.index(signature)
    utf16_start = len(content[:start_chars].encode("utf-16-le")) // 2
    utf16_end = utf16_start + len(signature.encode("utf-16-le")) // 2
    normalized = _normalize_polyglot_symbol(
        {
            "name": "default_export",
            "type": "DefaultExport",
            "start": utf16_start,
            "end": utf16_end,
            "signature": signature,
            "parserKind": "typescript_compiler_api",
        },
        content,
        language="typescript",
    )

    source_bytes = content.encode("utf-8")
    assert source_bytes[normalized["start"]:normalized["end"]].decode("utf-8") == signature


def test_typescript_offset_batch_uses_one_content_index_for_many_symbols():
    content = "\U0001f642" + ("x" * 512)
    raw_symbols = [
        {
            "name": f"icon_{index}",
            "type": "ReExportedSymbol",
            "start": 2 + index,
            "end": 3 + index,
            "parserKind": "typescript_compiler_api",
        }
        for index in range(512)
    ]

    with patch.object(
        generate_atlas_module,
        "_utf16_offset_to_utf8_byte_offset",
        side_effect=AssertionError("per-symbol source rescan"),
    ):
        normalized = _normalize_polyglot_symbols(
            raw_symbols,
            content,
            language="javascript",
        )

    assert len(normalized) == 512
    assert (normalized[0]["start"], normalized[0]["end"]) == (4, 5)
    assert (normalized[-1]["start"], normalized[-1]["end"]) == (515, 516)


def test_normalized_symbol_supplies_the_file_cache_state_flow_contract():
    symbol = _normalize_polyglot_symbol(
        {
            "name": "useStore",
            "type": "Function",
            "features": ["Tech:zustand"],
        },
        "def useStore(): pass\n",
        language="python",
    )
    file_data = {
        "ast_contract_version": generate_atlas_module.AST_CONTRACT_VERSION,
        "project_key": "MAIN",
        "atlas_rel_path": "store.py",
        "workspace_rel": "store.py",
        "repo_relative_path": "store.py",
        "target_ref": "MAIN::store.py",
        "symbols": [symbol],
        "parser_evidence": {"status": "observed", "reported_by_adapter": True, "parser_kind": "python_ast"},
    }

    assert isinstance(symbol["state_flow"], dict)
    assert file_contract_is_current(file_data)


@pytest.mark.parametrize("evidence, reusable", [
    (None, False),
    ({}, False),
    ({"status": "unavailable", "reported_by_adapter": True, "parser_kind": "typescript_compiler_api"}, False),
    ({"status": "degraded", "reported_by_adapter": True, "parser_kind": "unavailable"}, False),
    ({"status": "observed", "reported_by_adapter": False, "parser_kind": "typescript_compiler_api"}, False),
    ({"status": "observed", "reported_by_adapter": True, "parser_kind": "typescript_compiler_api"}, True),
    ({"status": "degraded", "reported_by_adapter": True, "parser_kind": "typescript_compiler_api", "parser_diagnostic_count": 1}, True),
])
def test_empty_file_cache_requires_actual_parser_evidence(evidence, reusable):
    record = {
        "ast_contract_version": generate_atlas_module.AST_CONTRACT_VERSION,
        "project_key": "MAIN", "atlas_rel_path": "empty.ts",
        "workspace_rel": "empty.ts", "repo_relative_path": "empty.ts",
        "target_ref": "MAIN::empty.ts", "symbols": [], "parser_evidence": evidence,
    }
    assert file_contract_is_current(record) is reusable


def test_typescript_offset_batch_preserves_invalid_surrogate_failure():
    with pytest.raises(ValueError, match="splits a surrogate pair"):
        _normalize_polyglot_symbols(
            [
                {
                    "name": "broken",
                    "type": "Variable",
                    "start": 1,
                    "end": 2,
                    "parserKind": "typescript_compiler_api",
                }
            ],
            "\U0001f642",
            language="typescript",
        )


def test_dense_barrel_symbol_occurrences_grow_linearly_without_file_dependency_fanout():
    def build_files(symbol_count: int) -> dict:
        return {
            "generated/public-api.mjs": {
                "internal_deps": [f"generated/Module{index}.mjs" for index in range(symbol_count)],
                "symbols": [
                    {
                        "name": f"proxy:./Module{index}.mjs",
                        "type": "ProxyExport",
                        "dependencies": [],
                        "module_specifier": f"./Module{index}.mjs",
                        "line": index + 1,
                    }
                    for index in range(symbol_count)
                ],
            }
        }

    small = _build_project_symbol_occurrences(build_files(128))
    large = _build_project_symbol_occurrences(build_files(256))

    assert all(len(row["dependencies"]) == 1 for row in large)
    assert large[-1]["dependencies"] == ["./Module255.mjs"]
    assert len(json.dumps(large, separators=(",", ":"))) < 2.2 * len(
        json.dumps(small, separators=(",", ":"))
    )


def test_previous_atlas_is_skipped_only_for_an_unbounded_complete_refresh():
    expected = {"MAIN", "PACKAGE_A"}

    assert previous_atlas_required_for_generation(
        ["MAIN", "PACKAGE_A"],
        expected,
        bounded_projection=False,
    ) is False
    assert previous_atlas_required_for_generation(
        ["MAIN"],
        expected,
        bounded_projection=False,
    ) is True
    assert previous_atlas_required_for_generation(
        ["MAIN", "PACKAGE_A"],
        expected,
        bounded_projection=True,
    ) is True
    assert previous_atlas_required_for_generation(
        None,
        expected,
        bounded_projection=False,
    ) is True


def test_typescript_compiler_offsets_preserve_explicit_zero_for_empty_source():
    for language in ("typescript", "javascript"):
        normalized = _normalize_polyglot_symbol(
            {
                "name": "__file_meta__",
                "type": "Meta",
                "start": 0,
                "end": 0,
                "parserKind": "typescript_compiler_api",
            },
            "",
            language=language,
        )

        assert normalized["start"] == 0
        assert normalized["end"] == 0

    leading_symbol = _normalize_polyglot_symbol(
        {
            "name": "x",
            "type": "Variable",
            "start": 0,
            "end": 1,
            "parserKind": "typescript_compiler_api",
        },
        "x",
        language="typescript",
    )
    assert leading_symbol["start"] == 0
    assert leading_symbol["end"] == 1


def test_typescript_compiler_offsets_reject_malformed_non_empty_spans():
    with pytest.raises(ValueError, match="exceeds source length"):
        _normalize_polyglot_symbol(
            {
                "name": "broken",
                "type": "Variable",
                "start": 0,
                "end": 2,
                "parserKind": "typescript_compiler_api",
            },
            "x",
            language="typescript",
        )

    with pytest.raises(ValueError, match="splits a surrogate pair"):
        _normalize_polyglot_symbol(
            {
                "name": "broken",
                "type": "Variable",
                "start": 1,
                "end": 2,
                "parserKind": "typescript_compiler_api",
            },
            "\U0001f642",
            language="typescript",
        )

    with pytest.raises(ValueError, match="end precedes start"):
        _normalize_polyglot_symbol(
            {
                "name": "broken",
                "type": "Variable",
                "start": 1,
                "end": 0,
                "parserKind": "typescript_compiler_api",
            },
            "x",
            language="typescript",
        )


def test_canonical_types_preserve_language_specific_structure():
    cases = [
        ({"name": "run", "type": "Method", "signature": "void run()"}, "java", "method"),
        ({"name": "Config", "type": "Struct", "signature": "struct Config"}, "go", "struct"),
        ({"name": "Mode", "type": "Enum", "signature": "enum Mode"}, "java", "enum"),
        ({"name": "Acme", "type": "Namespace", "signature": "namespace Acme"}, "csharp", "namespace"),
    ]
    for raw, language, expected in cases:
        normalized = _normalize_polyglot_symbol(raw, "", language)
        assert normalized["canonical_symbol_type"] == expected
        assert normalized["semantic_depth"] == "signature_only"
        assert normalized["logic_dna_kind"] == "structural_signature"
        assert normalized["normalization_confidence"] == "low"


def test_python_logic_dna_tracks_logic_not_formatting():
    with tempfile.TemporaryDirectory(prefix="python_logic_dna_") as tmp:
        root = Path(tmp)
        plain = root / "plain.py"
        formatted = root / "formatted.py"
        changed = root / "changed.py"
        plain.write_text("def compute(a: int):\n    return a + 1\n", encoding="utf-8")
        formatted.write_text("# noise\ndef compute(a: int):\n\n    return a + 1  # same logic\n", encoding="utf-8")
        changed.write_text("def compute(a: int):\n    return a + 2\n", encoding="utf-8")
        symbols = []
        for path in (plain, formatted, changed):
            symbols.append(next(item for item in sequence_python_file(str(path)) if item["name"] == "compute"))
        assert symbols[0]["logicDna"] == symbols[1]["logicDna"]
        assert symbols[0]["logicDna"] != symbols[2]["logicDna"]
        assert symbols[0]["normalizationProfile"] == "python_ast_v1"
        assert symbols[0]["semanticDepth"] == "ast_normalized"


def test_python_sequencer_reports_observed_ast_evidence():
    with tempfile.TemporaryDirectory(prefix="python_ast_evidence_") as tmp:
        path = Path(tmp) / "valid.py"
        path.write_text("def compute(value):\n    return value + 1\n", encoding="utf-8")
        result = sequence_python_file_with_evidence(str(path))
        assert result["status"] == "observed"
        assert result["parser_kind"] == "python_ast"
        assert result["semantic_depth"] == "ast_normalized"
        assert result["error_family"] is None
        assert result["symbols"]


def test_python_typing_stub_uses_ast_evidence_without_type_semantic_claim():
    with tempfile.TemporaryDirectory(prefix="python_stub_ast_evidence_") as tmp:
        path = Path(tmp) / "contract.pyi"
        path.write_text("class Contract:\n    value: str\n", encoding="utf-8")
        result = sequence_python_file_with_evidence(str(path))
        assert result["status"] == "observed"
        assert result["parser_kind"] == "python_ast"
        assert result["semantic_depth"] == "ast_normalized"
        assert any(symbol["name"] == "Contract" for symbol in result["symbols"])


def test_python_sequencer_reports_regex_fallback_as_degraded():
    with tempfile.TemporaryDirectory(prefix="python_ast_degraded_") as tmp:
        path = Path(tmp) / "invalid.py"
        path.write_text("def recoverable(:\n    return 1\n", encoding="utf-8")
        result = sequence_python_file_with_evidence(str(path))
        assert result["status"] == "degraded"
        assert result["parser_kind"] == "regex_fallback"
        assert result["semantic_depth"] == "unavailable"
        assert result["error_family"] == "python_ast_processing_error"
        assert all(item["parserKind"] == "regex_fallback" for item in result["symbols"])


def test_python_sequencer_reports_missing_source_as_unavailable():
    with tempfile.TemporaryDirectory(prefix="python_ast_missing_") as tmp:
        path = Path(tmp) / "missing.py"
        result = sequence_python_file_with_evidence(str(path))
        assert result["status"] == "unavailable"
        assert result["parser_kind"] == "unavailable"
        assert result["semantic_depth"] == "unavailable"
        assert result["error_family"] == "source_read_error"
        assert result["symbols"] == []


def test_python_class_methods_are_not_module_exports():
    with tempfile.TemporaryDirectory(prefix="python_method_exports_") as tmp:
        path = Path(tmp) / "handler.py"
        path.write_text(
            "from http.server import BaseHTTPRequestHandler\n"
            "class Handler(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        return None\n"
            "def module_api():\n"
            "    return None\n",
            encoding="utf-8",
        )
        symbols = sequence_python_file(str(path))
        do_get = next(item for item in symbols if item["name"] == "do_GET")
        module_api = next(item for item in symbols if item["name"] == "module_api")
        assert do_get["type"] == "Method"
        assert do_get["exported"] is False
        assert module_api["type"] == "Function"
        assert module_api["exported"] is True


def test_python_nested_functions_are_not_module_exports():
    with tempfile.TemporaryDirectory(prefix="python_nested_exports_") as tmp:
        path = Path(tmp) / "nested.py"
        path.write_text(
            "def outer():\n"
            "    def helper():\n"
            "        return 1\n"
            "    return helper()\n",
            encoding="utf-8",
        )
        symbols = sequence_python_file(str(path))
        outer = next(item for item in symbols if item["name"] == "outer")
        helper = next(item for item in symbols if item["name"] == "helper")
        assert outer["type"] == "Function"
        assert outer["exported"] is True
        assert helper["type"] == "NestedFunction"
        assert helper["exported"] is False


def test_python_import_scope_distinguishes_top_level_and_local_imports():
    with tempfile.TemporaryDirectory(prefix="python_import_scope_") as tmp:
        path = Path(tmp) / "imports.py"
        path.write_text(
            "import os\n"
            "def load_store():\n"
            "    from tools.core.artifact_store import STORE\n"
            "    return STORE\n",
            encoding="utf-8",
        )
        symbols = sequence_python_file(str(path))
        os_import = next(item for item in symbols if item["type"] == "Import" and item["name"] == "os")
        store_import = next(item for item in symbols if item["type"] == "Import" and item["name"] == "STORE")
        assert os_import["importScope"] == "top_level"
        assert "import_scope:top_level" in os_import["features"]
        assert store_import["importScope"] == "local"
        assert "import_scope:local" in store_import["features"]


def test_structural_sequencers_declare_honest_semantic_depth():
    with tempfile.TemporaryDirectory(prefix="structural_depth_") as tmp:
        root = Path(tmp)
        fixtures = [
            (root / "Demo.java", "public class Demo { public void run() {} }", sequence_java_file),
            (root / "demo.go", "package demo\ntype Config struct {}\nfunc Run() {}\n", sequence_go_file),
            (root / "Demo.cs", "namespace Acme; public class Demo { public void Run() {} }", sequence_cs_file),
        ]
        for path, source, sequencer in fixtures:
            path.write_text(source, encoding="utf-8")
            symbols = sequencer(str(path))
            assert symbols
            assert all(item["semanticDepth"] == "signature_only" for item in symbols)
            assert all(item["logicDnaKind"] == "structural_signature" for item in symbols)
            assert all(item["normalizationConfidence"] == "low" for item in symbols)


def test_structural_sequencers_report_missing_source_as_unavailable():
    with tempfile.TemporaryDirectory(prefix="structural_missing_") as tmp:
        root = Path(tmp)
        cases = [
            (root / "Missing.java", sequence_java_file_with_evidence),
            (root / "missing.go", sequence_go_file_with_evidence),
            (root / "Missing.cs", sequence_cs_file_with_evidence),
        ]
        for path, sequencer in cases:
            result = sequencer(str(path))
            assert result["status"] == "unavailable"
            assert result["parser_kind"] == "unavailable"
            assert result["semantic_depth"] == "unavailable"
            assert result["error_family"] == "source_read_error"
            assert result["symbols"] == []


def test_node_sequencer_reports_observed_degraded_and_unavailable_evidence():
    with tempfile.TemporaryDirectory(prefix="node_ast_evidence_") as tmp:
        root = Path(tmp)
        valid_path = root / "valid.ts"
        invalid_path = root / "invalid.ts"
        missing_path = root / "missing.ts"
        valid_path.write_text("export const compute = (value: number) => value + 1;\n", encoding="utf-8")
        invalid_path.write_text("export const broken = (: number) => 1;\n", encoding="utf-8")

        observed = next(item for item in _sequence_node_file(valid_path) if item["name"] == "__file_meta__")
        degraded = next(item for item in _sequence_node_file(invalid_path) if item["name"] == "__file_meta__")
        unavailable = next(item for item in _sequence_node_file(missing_path) if item["name"] == "__file_meta__")

        assert observed["parserStatus"] == "observed"
        assert observed["parserDiagnosticCount"] == 0
        assert degraded["parserStatus"] == "degraded"
        assert degraded["parserDiagnosticCount"] > 0
        assert degraded["semanticDepth"] == "partial_ast"
        assert unavailable["parserStatus"] == "unavailable"
        assert unavailable["semanticDepth"] == "unavailable"
        assert "Error:FileReadFailed" in unavailable["features"]


def test_generate_atlas_smoke_indexes_polyglot_symbols_and_imports():
    with tempfile.TemporaryDirectory(prefix="polyglot_atlas_contract_") as tmp:
        root = Path(tmp)
        (root / "pkg").mkdir()
        (root / "pkg" / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
        (root / "app.py").write_text("from pkg.service import run as execute\nclass App: pass\n", encoding="utf-8")
        (root / "lazy.py").write_text(
            "def load():\n    from pkg.service import run\n    return run()\n\ndef run():\n    return 2\n",
            encoding="utf-8",
        )
        (root / "broken.py").write_text("def recoverable(:\n    return 1\n", encoding="utf-8")
        (root / "valid.ts").write_text("export const compute = (value: number) => value + 1;\n", encoding="utf-8")
        (root / "broken.ts").write_text("export const broken = (: number) => 1;\n", encoding="utf-8")
        (root / "module.mjs").write_text("export const moduleValue = 1;\n", encoding="utf-8")
        (root / "config.cjs").write_text("module.exports = { enabled: true };\n", encoding="utf-8")
        (root / "typed.mts").write_text("export const typedValue: number = 1;\n", encoding="utf-8")
        (root / "legacy.cts").write_text("export const legacyValue: number = 1;\n", encoding="utf-8")
        for empty_name in ("empty.ts", "empty.tsx", "empty.js", "empty.jsx"):
            (root / empty_name).write_text("", encoding="utf-8")
        java_dir = root / "com" / "acme"
        java_dir.mkdir(parents=True)
        (java_dir / "Service.java").write_text("package com.acme; public class Service {}\n", encoding="utf-8")
        (root / "Demo.java").write_text("import com.acme.Service; public class Demo {}\n", encoding="utf-8")
        go_dir = root / "book" / "internal"
        go_dir.mkdir(parents=True)
        (go_dir / "index.go").write_text("package internal\n", encoding="utf-8")
        (root / "main.go").write_text('package main\nimport "book/internal/index"\nfunc main() {}\n', encoding="utf-8")
        cs_dir = root / "My" / "App"
        cs_dir.mkdir(parents=True)
        (cs_dir / "Core.cs").write_text("namespace My.App; public class Core {}\n", encoding="utf-8")
        (root / "Demo.cs").write_text("using My.App.Core; namespace Demo; public class DemoService {}\n", encoding="utf-8")
        env = dict(os.environ)
        for key in ("CODEMAPS_TARGET_PREFLIGHT_RECEIPT", "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256", "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE", "CODEMAPS_TARGET_PROJECTS"):
            env.pop(key, None)
        env["CODEMAPS_TARGET_ROOT"] = str(root)
        result = subprocess.run(
            [sys.executable, "-m", "tools.engines.generate_atlas"],
            cwd=str(CODE_MAPS_DIR),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        safe_prefix = "".join(ch.lower() if ch.isalnum() else "_" for ch in root.name).strip("_")
        candidates = sorted((CODE_MAPS_DIR / "output" / "external_targets").glob(f"{safe_prefix}_*/.raw/atlas.json"))
        assert candidates
        atlas = json.loads(candidates[-1].read_text(encoding="utf-8"))
        ensure_against_schema(CONFIG_DIR / "schemas" / "atlas.schema.json", "atlas", atlas)
        project = atlas["MAIN"]["project"]
        assert project["language"].startswith("polyglot:")
        assert project["ast_contract_version"] == "polyglot-v1"
        sequencer_evidence = project["sequencer_evidence"]
        assert sequencer_evidence["scope"] == "materialized_project_files"
        assert sequencer_evidence["repository_wide_claim"] is False
        python_coverage = next(
            item for item in sequencer_evidence["coverage"]["details"]
            if item["strategy"].startswith("python-ast")
        )
        assert python_coverage["claim_status"] == "degraded"
        assert python_coverage["files_reported_by_adapter"] == 4
        assert python_coverage["files_accounted"] == 4
        assert python_coverage["status_counts"]["observed"] == 3
        assert python_coverage["status_counts"]["degraded"] == 1
        assert python_coverage["status_counts"]["unavailable"] == 0
        assert python_coverage["degraded_file_samples"][0]["path"] == "broken.py"
        node_coverage = next(
            item for item in sequencer_evidence["coverage"]["details"]
            if item["strategy"] == "node-ast"
        )
        assert node_coverage["claim_status"] == "degraded"
        assert node_coverage["files_reported_by_adapter"] == 10
        assert node_coverage["files_accounted"] == 10
        assert node_coverage["status_counts"] == {"observed": 9, "degraded": 1, "unavailable": 0}
        assert node_coverage["non_observed_file_samples"][0]["path"].endswith("broken.ts")
        structural_coverage = {
            item["strategy"]: item
            for item in sequencer_evidence["coverage"]["details"]
            if item["strategy"] in {"java-regex", "go-regex", "cs-regex"}
        }
        assert set(structural_coverage) == {"java-regex", "go-regex", "cs-regex"}
        assert all(item["claim_status"] == "proven" for item in structural_coverage.values())
        assert all(item["files_reported_by_adapter"] == item["files_accounted"] for item in structural_coverage.values())
        assert all(item["status_counts"]["unavailable"] == 0 for item in structural_coverage.values())
        files = atlas["MAIN"]["files"]
        for module_path in ("module.mjs", "config.cjs", "typed.mts", "legacy.cts"):
            assert module_path in files
        for module_path in ("empty.ts", "empty.tsx", "empty.js", "empty.jsx"):
            assert module_path in files
            assert files[module_path]["symbols"] == []
            assert files[module_path]["exports"] == []
            assert files[module_path]["imports"] == []
            assert files[module_path]["internal_deps"] == []
        for module_path in ("module.mjs", "typed.mts", "legacy.cts"):
            assert files[module_path]["symbols"]
        global_run_occurrences = [
            item for item in atlas["MAIN"]["symbols"]
            if item.get("name") == "run" and item.get("file") in {"pkg/service.py", "lazy.py"}
        ]
        assert {item["file"] for item in global_run_occurrences} == {"pkg/service.py", "lazy.py"}
        assert all(isinstance(item.get("line"), int) for item in global_run_occurrences)
        assert len({(item["file"], item["line"], item["type"]) for item in global_run_occurrences}) == len(global_run_occurrences)
        assert "pkg/service.py" in files["app.py"]["imports"]
        assert "com/acme/Service.java" in files["Demo.java"]["imports"]
        assert "book/internal/index.go" in files["main.go"]["imports"]
        assert "My/App/Core.cs" in files["Demo.cs"]["imports"]
        assert files["app.py"]["import_records"][0]["raw_source"] == "pkg/service"
        assert files["app.py"]["import_records"][0]["name"] == "run"
        assert files["app.py"]["import_records"][0]["scope"] == "top_level"
        assert "pkg/service.py" in files["app.py"]["internal_deps"]
        assert "pkg/service.py" not in files["app.py"].get("lazy_internal_deps", [])
        assert files["lazy.py"]["import_records"][0]["raw_source"] == "pkg/service"
        assert files["lazy.py"]["import_records"][0]["scope"] == "local"
        assert "pkg/service.py" not in files["lazy.py"]["internal_deps"]
        assert "pkg/service.py" in files["lazy.py"].get("lazy_internal_deps", [])
        assert files["Demo.java"]["import_records"][0]["raw_source"] == "com/acme/Service"
        assert files["main.go"]["import_records"][0]["raw_source"] == "book/internal/index"
        assert files["Demo.cs"]["import_records"][0]["raw_source"] == "My/App/Core"
        python_function = next(item for item in files["pkg/service.py"]["symbols"] if item["name"] == "run")
        java_class = next(item for item in files["Demo.java"]["symbols"] if item["name"] == "Demo")
        go_function = next(
            item for item in files["main.go"]["symbols"]
            if item["name"] == "main" and item["canonical_symbol_type"] == "function"
        )
        csharp_namespace = next(item for item in files["Demo.cs"]["symbols"] if item["canonical_symbol_type"] == "namespace")
        assert python_function["semantic_depth"] == "ast_normalized"
        assert python_function["line"] == 1
        assert python_function["end_line"] == 2
        assert python_function["source_lines"] == "L1-L2"
        assert java_class["canonical_symbol_type"] == "class"
        assert go_function["canonical_symbol_type"] == "function"
        assert csharp_namespace["normalization_profile"] == "csharp_structural_v1"
        broken_function = next(item for item in files["broken.py"]["symbols"] if item["name"] == "recoverable")
        assert broken_function["parser_kind"] == "regex_fallback"
        assert broken_function["semantic_depth"] == "unavailable"
