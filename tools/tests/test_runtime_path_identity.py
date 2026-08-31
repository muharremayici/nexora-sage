import json
from pathlib import Path
from unittest.mock import patch

from tools.core import config
from tools.core.installation_identity import (
    is_sage_installation_root,
    runtime_installation_excluded_roots,
)
from tools.engines import quant_engine
from tools.orchestrators import discovery


def test_quant_file_resolution_uses_runtime_roots_not_legacy_installation_names(tmp_path):
    root = Path(tmp_path)
    installation = root / "Kurulum-Özel-7f3"
    source = installation / "tools" / "worker.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")

    with (
        patch.object(quant_engine, "ROOT", root),
        patch.object(quant_engine, "CODE_MAPS_DIR", installation),
        patch.object(quant_engine, "MAIN_PROJECT_ROOT", root / "src"),
    ):
        assert quant_engine._existing_file_for(
            "Kurulum-Özel-7f3/tools/worker.py"
        ) == source.resolve()
        assert quant_engine._existing_file_for("tools/worker.py") == source.resolve()
        assert quant_engine._existing_file_for(
            "Legacy Product Folder/tools/worker.py"
        ) is None


def test_runtime_installation_exclusion_is_resolved_path_based(tmp_path):
    target = tmp_path / "target"
    installation = target / "Kurulum-Özel-7f3"
    unrelated = target / "Legacy Product Folder"
    installation.mkdir(parents=True)
    unrelated.mkdir()

    assert runtime_installation_excluded_roots(target, installation) == {
        installation.resolve()
    }
    assert runtime_installation_excluded_roots(installation, installation) == set()
    assert unrelated.resolve() not in runtime_installation_excluded_roots(
        target,
        installation,
    )


def test_sage_installation_identity_uses_package_metadata_not_directory_name(tmp_path):
    renamed_installation = tmp_path / "Kurulum-Özel-7f3"
    renamed_installation.mkdir()
    (renamed_installation / "sage.py").write_text("", encoding="utf-8")
    (renamed_installation / "codemaps.py").write_text("", encoding="utf-8")
    (renamed_installation / "pyproject.toml").write_text(
        "\n".join(
            (
                "[project]",
                'name = "nexora-sage"',
                "[tool.nexora_sage.distribution]",
                'mode = "source_checkout"',
            )
        ),
        encoding="utf-8",
    )
    legacy_named_directory = tmp_path / "Legacy Product Folder"
    legacy_named_directory.mkdir()
    (legacy_named_directory / "sage.py").write_text("", encoding="utf-8")
    (legacy_named_directory / "codemaps.py").write_text("", encoding="utf-8")
    malformed_metadata = tmp_path / "Bozuk-Metadata"
    malformed_metadata.mkdir()
    (malformed_metadata / "sage.py").write_text("", encoding="utf-8")
    (malformed_metadata / "codemaps.py").write_text("", encoding="utf-8")
    (malformed_metadata / "pyproject.toml").write_bytes(b"\xff")

    assert is_sage_installation_root(renamed_installation) is True
    assert is_sage_installation_root(legacy_named_directory) is False
    assert is_sage_installation_root(malformed_metadata) is False


def test_discovery_traversal_excludes_only_the_running_installation(tmp_path):
    target = tmp_path / "target"
    installation = target / "Kurulum-Özel-7f3"
    unrelated = target / "Legacy Product Folder"
    installation.mkdir(parents=True)
    unrelated.mkdir()
    (target / "app.ts").write_text("export const app = true;\n", encoding="utf-8")
    (installation / "private.py").write_text("PRIVATE = True\n", encoding="utf-8")
    (unrelated / "normal.go").write_text("package normal\n", encoding="utf-8")

    with (
        patch.object(discovery, "ROOT_DIR", target),
        patch.object(discovery, "CODE_MAPS_DIR", installation),
        patch.object(
            discovery,
            "LANGUAGE_MAP",
            {".go": "go", ".py": "python", ".ts": "typescript"},
        ),
        patch.object(discovery, "SKIP_DIRS", {"node_modules"}),
    ):
        languages, is_polyglot = discovery.detect_languages(target)

    assert languages == ["go", "typescript"]
    assert is_polyglot is True


def test_target_config_traversal_uses_runtime_identity_and_preserves_self_target(tmp_path):
    target = tmp_path / "target"
    installation = target / "Kurulum-Özel-7f3"
    unrelated = target / "Legacy Product Folder"
    installation.mkdir(parents=True)
    unrelated.mkdir()
    (installation / "private.py").write_text("PRIVATE = True\n", encoding="utf-8")
    (installation / "tsconfig.private.json").write_text(
        json.dumps({"compilerOptions": {"paths": {"@private/*": ["./private/*"]}}}),
        encoding="utf-8",
    )
    (unrelated / "normal.py").write_text("NORMAL = True\n", encoding="utf-8")
    (unrelated / "tsconfig.normal.json").write_text(
        json.dumps({"compilerOptions": {"paths": {"@normal/*": ["./normal/*"]}}}),
        encoding="utf-8",
    )

    with patch.object(config, "CODE_MAPS_DIR", installation):
        assert config._target_has_source_ext(target, (".py",)) is True
        aliases = config._observe_target_path_aliases(target)
        assert aliases["aliases"] == ["@normal/*"]
        assert config._target_has_source_ext(installation, (".py",)) is True
