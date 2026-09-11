from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
SEQUENCER = CODE_MAPS_DIR / "tools" / "engines" / "ast_sequencer.cjs"


def _sequence(path: Path) -> list[dict]:
    result = subprocess.run(
        ["node", str(SEQUENCER), str(path)], cwd=CODE_MAPS_DIR,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_ast_import_evidence_excludes_generated_text_and_classifies_edges(tmp_path: Path):
    target = tmp_path / "imports.ts"
    target.write_text(
        """
const generated = `import { Fabricated } from './fabricated';`;
const commented = "require('./also-fabricated')";
// import './comment-fabricated';
import type { TypeShape } from './types';
import RuntimeDefault, { RuntimeThing, type RuntimeShape } from './runtime';
import './setup';
export { RuntimeExport, type ExportShape } from './reexport';
const lazy = import('./lazy');
const legacy = require('./legacy');
async function loadNamed() {
  const { NamedOne: localOne } = await import('./named-one');
  return import('./named-two').then(({ NamedTwo }) => NamedTwo);
}
""".strip() + "\n", encoding="utf-8",
    )
    meta = next(item for item in _sequence(target) if item["name"] == "__file_meta__")
    assert meta["moduleImports"] == [
        {"source": "./types", "name": "TypeShape", "kind": "type", "scope": "top_level"},
        {"source": "./runtime", "name": "default", "kind": "default", "scope": "top_level"},
        {"source": "./runtime", "name": "RuntimeThing", "kind": "named", "scope": "top_level"},
        {"source": "./runtime", "name": "RuntimeShape", "kind": "type", "scope": "top_level"},
        {"source": "./setup", "name": "*", "kind": "side_effect", "scope": "top_level"},
        {"source": "./reexport", "name": "RuntimeExport", "kind": "reexport", "scope": "top_level"},
        {"source": "./reexport", "name": "ExportShape", "kind": "type", "scope": "top_level"},
        {"source": "./lazy", "name": "*", "kind": "dynamic", "scope": "top_level"},
        {"source": "./legacy", "name": "*", "kind": "require", "scope": "top_level"},
        {"source": "./named-one", "name": "*", "kind": "dynamic", "scope": "local"},
        {"source": "./named-one", "name": "NamedOne", "kind": "dynamic_member", "scope": "local"},
        {"source": "./named-two", "name": "*", "kind": "dynamic", "scope": "local"},
        {"source": "./named-two", "name": "NamedTwo", "kind": "dynamic_member", "scope": "local"},
    ]


def test_atlas_materializes_only_syntax_grounded_eager_and_lazy_edges():
    with tempfile.TemporaryDirectory(prefix="typescript_import_evidence_") as tmp:
        root = Path(tmp)
        (root / "real.ts").write_text("export const real = true;\n", encoding="utf-8")
        (root / "lazy.ts").write_text("export const lazy = true;\n", encoding="utf-8")
        (root / "generator.ts").write_text(
            "const generated = `import { Fake } from './fabricated';`;\n"
            "import { real } from './real';\nconst deferred = import('./lazy');\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        for key in (
            "CODEMAPS_TARGET_PREFLIGHT_RECEIPT",
            "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256",
            "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE",
            "CODEMAPS_TARGET_PROJECTS",
        ):
            env.pop(key, None)
        env["CODEMAPS_TARGET_ROOT"] = str(root)
        result = subprocess.run(
            [sys.executable, "-m", "tools.engines.generate_atlas"], cwd=CODE_MAPS_DIR,
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        prefix = "".join(ch.lower() if ch.isalnum() else "_" for ch in root.name).strip("_")
        candidates = sorted((CODE_MAPS_DIR / "output" / "external_targets").glob(f"{prefix}_*/.raw/atlas.json"))
        assert candidates
        atlas = json.loads(candidates[-1].read_text(encoding="utf-8"))
        file_data = atlas["MAIN"]["files"]["generator.ts"]
        assert {record["raw_source"] for record in file_data["import_records"]} == {"./real", "./lazy"}
        assert all("fabricated" not in source for key in ("imports", "internal_deps", "lazy_internal_deps") for source in file_data[key])
        assert any(Path(source).stem == "real" for source in file_data["internal_deps"])
        assert any(Path(source).stem == "lazy" for source in file_data["lazy_internal_deps"])
