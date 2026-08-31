from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.engines.dead_code_detector import DEAD_CODE_ENGINE_SIGNATURE, DeadCodeDetector
from tools.core.package_contracts import build_package_public_contracts


RAW_OUTPUT_PATH = RAW_DIR / "dead_code_public_contract_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "dead_code_public_contract_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _public_export_chain_fixture() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nexora_dead_code_public_") as tmp:
        root = Path(tmp)
        _write(
            root / "package.json",
            json.dumps(
                {
                    "name": "@fixture/public-contract",
                    "exports": {"." : "./src/index.ts"},
                    "types": "./src/index.ts",
                },
                indent=2,
            ),
        )
        _write(root / "src" / "index.ts", "export * from './components';\n")
        _write(root / "src" / "components" / "index.ts", "export * from './Button';\n")
        _write(root / "src" / "components" / "Button.ts", "export function Button() { return null; }\n")

        detector = DeadCodeDetector()
        detector.projects = {"FIXTURE": root}
        files = {
            "src/index.ts": {
                "exports": [
                    {"type": "ProxyExport", "name": "proxy:src/components/index.ts", "moduleSpecifier": "./components"}
                ]
            },
            "src/components/index.ts": {
                "exports": [
                    {"type": "ProxyExport", "name": "proxy:src/components/Button.ts", "moduleSpecifier": "./Button"}
                ]
            },
            "src/components/Button.ts": {
                "exports": [{"type": "Function", "name": "Button"}],
                "symbols": [{"name": "Button", "type": "Function", "exported": True}],
            },
        }
        public_contracts = build_package_public_contracts(root, [root / "package.json"])
        public_files, public_symbols = detector._public_export_chain_surfaces("FIXTURE", files, public_contracts)
        return {
            "public_files": sorted(public_files),
            "public_symbols": sorted(f"{path}::{symbol}" for path, symbol in public_symbols),
            "deep_symbol_protected": "src/components/Button.ts" in public_files
            or ("src/components/Button.ts", "Button") in public_symbols,
        }


def _named_public_reexport_fixture() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nexora_dead_code_named_public_") as tmp:
        root = Path(tmp)
        _write(root / "package.json", json.dumps({"exports": "./src/index.ts"}, indent=2))
        _write(root / "src" / "index.ts", "export { Button } from './Button';\n")
        _write(root / "src" / "Button.ts", "export function Button() { return null; }\n")

        detector = DeadCodeDetector()
        detector.projects = {"FIXTURE": root}
        files = {
            "src/index.ts": {
                "exports": [
                    {
                        "type": "ReExportedSymbol",
                        "name": "Button",
                        "moduleSpecifier": "./Button",
                        "dependencies": ["Button"],
                        "exportedNames": ["Button"],
                    }
                ]
            },
            "src/Button.ts": {
                "exports": [{"type": "Function", "name": "Button"}],
                "symbols": [{"name": "Button", "type": "Function", "exported": True}],
            },
        }
        public_contracts = build_package_public_contracts(root, [root / "package.json"])
        public_files, public_symbols = detector._public_export_chain_surfaces("FIXTURE", files, public_contracts)
        return {
            "public_files": sorted(public_files),
            "public_symbols": sorted(f"{path}::{symbol}" for path, symbol in public_symbols),
            "named_symbol_protected": ("src/Button.ts", "Button") in public_symbols,
        }


def _typescript_declaration_fixture() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nexora_dead_code_aug_") as tmp:
        root = Path(tmp)
        _write(
            root / "src" / "theme.d.ts",
            "declare module '@mui/material/styles' { interface Theme { sidebarWidth: number } }\n",
        )
        detector = DeadCodeDetector()
        detector.projects = {"FIXTURE": root}
        match = detector._match_contract_registry("FIXTURE", "src/theme.d.ts", "Theme")
        return {
            "matched": bool(match),
            "match": match or {},
            "reason": (match or {}).get("reason"),
        }


def run_validation() -> dict[str, Any]:
    dead_code_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "dead_code_detector.py")
    doctrine_text = _read(CODE_MAPS_DIR / "config" / "architecture_doctrine.json")
    artifact_payload = load_json_file(RAW_DIR / "dead_code.json", {})
    meta = artifact_payload.get("meta", {}) if isinstance(artifact_payload, dict) else {}

    star_fixture = _public_export_chain_fixture()
    named_fixture = _named_public_reexport_fixture()
    declaration_fixture = _typescript_declaration_fixture()

    checks = [
        _check(
            "dead_code_engine_signature_tracks_public_contract_logic",
            "atlas_public_contracts" in DEAD_CODE_ENGINE_SIGNATURE,
            DEAD_CODE_ENGINE_SIGNATURE,
        ),
        _check(
            "package_exports_are_scanned_for_public_surfaces",
            "_package_public_export_patterns" in dead_code_text
            and "public_contracts" in dead_code_text
            and ".rglob(" not in dead_code_text,
            "tools/engines/dead_code_detector.py",
        ),
        _check(
            "deep_star_barrel_export_chain_protects_deep_symbol",
            bool(star_fixture["deep_symbol_protected"]),
            star_fixture,
        ),
        _check(
            "named_reexport_chain_protects_named_symbol",
            bool(named_fixture["named_symbol_protected"]),
            named_fixture,
        ),
        _check(
            "typescript_declaration_and_augmentation_contracts_are_doctrine_driven",
            "typescript_declaration_contracts" in doctrine_text
            and "typescript_augmentation_contracts" in doctrine_text
            and bool(declaration_fixture["matched"]),
            declaration_fixture,
        ),
        _check(
            "dead_code_artifact_exposes_contract_exclusion_channels",
            isinstance(artifact_payload, dict)
            and "compatibility_exclusions" in artifact_payload
            and "contract_registry_exclusions" in artifact_payload
            and "type_only_export_exclusions" in artifact_payload
            and "classification_reason_counts" in meta,
            {
                "artifact": "output/.raw/dead_code.json",
                "meta_keys": sorted(meta.keys()),
                "contract_registry_exclusions": len(artifact_payload.get("contract_registry_exclusions", []) if isinstance(artifact_payload, dict) else []),
            },
        ),
    ]

    summary = {
        "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "total_checks": len(checks),
        "passed_checks": sum(1 for check in checks if check.get("passed")),
        "failed_checks": sum(1 for check in checks if not check.get("passed")),
        "generated_at": _utc_now(),
    }
    payload = {
        "meta": {
            "kind": "dead_code_public_contract_validation",
            "version": "v1",
            "generator": "tools.validate_dead_code_public_contracts",
        },
        "summary": summary,
        "checks": checks,
    }
    save_json_atomic(RAW_OUTPUT_PATH, payload)

    lines = [
        "# Dead Code Public Contract Validation",
        "",
        f"- Status: `{summary['status']}`",
        f"- Total checks: `{summary['total_checks']}`",
        f"- Passed: `{summary['passed_checks']}`",
        f"- Failed: `{summary['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        details = json.dumps(check.get("details"), ensure_ascii=False)
        details = details.replace("|", "\\|")
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {details} |")
    save_text_atomic(REPORT_OUTPUT_PATH, "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
