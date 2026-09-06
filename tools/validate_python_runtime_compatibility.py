from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Iterable

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent


def distribution_python_files(root: Path = CODE_MAPS_DIR) -> list[Path]:
    """Return Python sources shipped by the source-checkout distribution."""
    candidates = [*root.glob("*.py"), *(root / "tools").rglob("*.py")]
    return sorted({path.resolve() for path in candidates if path.is_file()})


def compile_python_sources(paths: Iterable[Path]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for path in paths:
        try:
            compile(path.read_bytes(), str(path), "exec")
        except (OSError, SyntaxError, ValueError) as exc:
            try:
                relative_path = path.resolve().relative_to(CODE_MAPS_DIR).as_posix()
            except ValueError:
                relative_path = str(path)
            failures.append(
                {
                    "path": relative_path,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
    return failures


def declared_python_requirement(root: Path = CODE_MAPS_DIR) -> str:
    payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(payload.get("project", {}).get("requires-python") or "")


def declared_tested_python_versions(root: Path = CODE_MAPS_DIR) -> list[str]:
    payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    versions = (
        payload.get("tool", {})
        .get("nexora_sage", {})
        .get("distribution", {})
        .get("tested_python_versions", [])
    )
    return [str(version) for version in versions]


def run_validation(root: Path = CODE_MAPS_DIR) -> dict[str, object]:
    files = distribution_python_files(root)
    failures = compile_python_sources(files)
    return {
        "meta": {
            "kind": "python_runtime_compatibility_validation",
            "version": "v1",
            "interpreter": ".".join(str(part) for part in sys.version_info[:3]),
            "implementation": sys.implementation.name,
            "declared_requirement": declared_python_requirement(root),
            "tested_python_versions": declared_tested_python_versions(root),
        },
        "summary": {
            "status": "PASS" if not failures else "FAIL",
            "source_files": len(files),
            "failures": len(failures),
        },
        "failure_details": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile all shipped SAGE Python sources with the active interpreter."
    )
    parser.parse_args()
    payload = run_validation()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
