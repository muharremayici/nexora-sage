import tempfile
from pathlib import Path
from unittest.mock import patch

from tools.core.artifact_validator import ensure_against_schema
from tools.core.config import CONFIG_DIR
from tools.core.path_engine import reset_path_resolution_caches, resolve_project_import
from tools.core.polyglot_imports import extract_go_qualified_imports
from tools.engines.ast_sequencer_cs import sequence_cs_file
from tools.engines.ast_sequencer_go import sequence_go_file
from tools.engines.ast_sequencer_java import sequence_java_file
from tools.engines.dead_code_detector import DeadCodeDetector


def test_go_module_qualified_import_resolves_to_physical_package():
    with tempfile.TemporaryDirectory(prefix="go_module_resolution_") as tmp:
        root = Path(tmp)
        module_root = root / "backend-go"
        config_dir = module_root / "internal" / "config"
        caller_dir = module_root / "cmd" / "server"
        config_dir.mkdir(parents=True)
        caller_dir.mkdir(parents=True)
        (module_root / "go.mod").write_text(
            "module github.com/example/product/backend-go\n\ngo 1.22\n",
            encoding="utf-8",
        )
        (config_dir / "config.go").write_text(
            "package config\n\nfunc LoadConfig() {}\n",
            encoding="utf-8",
        )
        reset_path_resolution_caches()

        with patch(
            "tools.core.path_engine.os.walk",
            side_effect=AssertionError("Go module resolution must not crawl the repository"),
        ):
            resolved = resolve_project_import(
                "github.com/example/product/backend-go/internal/config",
                str(caller_dir),
                str(root),
                str(root),
                language="go",
            )

        assert resolved == "backend-go/internal/config"
        assert resolve_project_import(
            "github.com/external/dependency",
            str(caller_dir),
            str(root),
            str(root),
            language="go",
        ) == "github.com/external/dependency"


def test_go_qualified_import_extraction_preserves_package_member_evidence():
    records = extract_go_qualified_imports(
        'import cfg "github.com/example/product/internal/config"\n'
        'import "github.com/example/product/internal/logging"\n'
        "func main() { cfg.LoadConfig(); logging.Configure() }\n"
    )

    assert records == [
        {
            "source": "github.com/example/product/internal/config",
            "name": "LoadConfig",
            "kind": "qualified-member",
            "alias": "cfg",
        },
        {
            "source": "github.com/example/product/internal/logging",
            "name": "Configure",
            "kind": "qualified-member",
            "alias": "logging",
        },
    ]


def test_go_package_member_evidence_selects_exact_exporting_source_file():
    available_files = {
        "backend-go/cmd/server/main.go",
        "backend-go/internal/config/config.go",
        "backend-go/internal/config/defaults.go",
    }
    package_files = {
        "backend-go/internal/config": {
            "backend-go/internal/config/config.go",
            "backend-go/internal/config/defaults.go",
        }
    }
    package_symbols = {
        ("backend-go/internal/config", "LoadConfig"): {
            "backend-go/internal/config/config.go"
        }
    }

    assert DeadCodeDetector._import_record_target_files(
        "backend-go/internal/config",
        "LoadConfig",
        available_files,
        package_files,
        package_symbols,
    ) == {"backend-go/internal/config/config.go"}
    assert DeadCodeDetector._import_record_target_files(
        "backend-go/internal/config",
        "UnknownMember",
        available_files,
        package_files,
        package_symbols,
    ) == package_files["backend-go/internal/config"]


def test_dead_code_v3_schema_accepts_contract_triage_and_unknown_reachability():
    payload = {
        "meta": {
            "version": "dead_code_v3",
            "generated_by": "dead_code_detector",
            "runtime_seconds": 0.01,
            "imported_file_edges": 1,
            "consumed_symbol_edges": 1,
            "proxy_edges": 0,
            "classification_reason_counts": {},
        },
        "summary": {"total": 0, "high": 0, "medium": 0, "low": 0},
        "items": [],
        "by_project": {},
        "contract_triage": [
            {
                "project": "MAIN",
                "file": "src/service.java",
                "symbol": "Service",
                "reason": "framework_runtime_decorator_contract",
                "contract_class": "FRAMEWORK_RUNTIME_CONTRACT_CANDIDATE",
                "contract_status": "runtime_binding_unknown",
                "recommended_action": "verify_component_scan_or_retain_contract",
                "mutation_proposed": False,
                "evidence": {"annotation_marker": "@Service"},
            }
        ],
        "unresolved_reachability": [
            {
                "project": "MAIN",
                "file": "src/worker.go",
                "symbol": "Run",
                "reason": "language_reachability_unavailable",
                "status": "UNKNOWN",
                "language": "go",
                "capability_mode": "inventory_only",
                "recommended_action": "retain_and_obtain_language_reachability_evidence",
                "mutation_proposed": False,
                "evidence": {
                    "parser_status": "observed",
                    "parser_kind": "go_structural",
                    "semantic_depth": "structural",
                },
            }
        ],
        "compatibility_exclusions": [],
        "legacy_boundary_exclusions": [],
        "contract_surface_exclusions": [],
        "constant_surface_exclusions": [],
        "type_surface_exclusions": [],
    }

    ensure_against_schema(
        CONFIG_DIR / "schemas" / "dead_code.schema.json",
        "dead_code",
        payload,
    )


def test_structural_sequencers_do_not_promote_members_or_private_types_to_module_exports():
    with tempfile.TemporaryDirectory(prefix="structural_export_semantics_") as tmp:
        root = Path(tmp)
        go_path = root / "sample.go"
        java_path = root / "Sample.java"
        cs_path = root / "Sample.cs"
        go_path.write_text(
            "package sample\ntype Public struct{}\ntype private struct{}\n"
            "func Exported() {}\nfunc internal() {}\nfunc (Public) Method() {}\n",
            encoding="utf-8",
        )
        java_path.write_text(
            "public class PublicType { public void run() {} }\nclass PackageType {}\n",
            encoding="utf-8",
        )
        cs_path.write_text(
            "namespace Acme; public class PublicType { public void Run() {} } internal class InternalType {}\n",
            encoding="utf-8",
        )

        go_symbols = sequence_go_file(go_path)
        java_symbols = sequence_java_file(java_path)
        cs_symbols = sequence_cs_file(cs_path)

        assert next(row for row in go_symbols if row["name"] == "Exported")["exported"] is True
        assert next(row for row in go_symbols if row["name"] == "internal")["exported"] is False
        assert next(row for row in go_symbols if row["name"] == "Method")["exported"] is False
        assert next(row for row in java_symbols if row["name"] == "PublicType")["exported"] is True
        assert next(row for row in java_symbols if row["name"] == "PackageType")["exported"] is False
        assert next(row for row in java_symbols if row["name"] == "run")["exported"] is False
        assert next(row for row in cs_symbols if row["name"] == "PublicType")["exported"] is True
        assert next(row for row in cs_symbols if row["name"] == "InternalType")["exported"] is False
        assert next(row for row in cs_symbols if row["name"] == "Run")["exported"] is False
