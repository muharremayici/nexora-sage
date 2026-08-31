from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.external_target_retention import DEFAULT_KEEP_PER_PREFIX, prune_generated_external_target_fixtures
from tools.core.operational_limits import external_polyglot_smoke_timeout_seconds
from tools.core.subprocess_telemetry import run_observed_subprocess


def _safe_prefix(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")


def _run(command: list[str], env: dict[str, str]):
    result, _duration = run_observed_subprocess(
        command,
        cwd=CODE_MAPS_DIR,
        env=env,
        label="external_polyglot_generate_atlas",
        timeout=external_polyglot_smoke_timeout_seconds(),
        log=lambda message: print(f"[external-polyglot-smoke] {message}", flush=True),
    )
    return result


def _write_fixture(root: Path) -> None:
    (root / "pkg").mkdir(parents=True)
    (root / "go" / "pkg").mkdir(parents=True)
    (root / "java" / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)

    (root / "app.py").write_text(
        "from pkg.service import PyService\n\nclass App:\n    def run(self):\n        return PyService().run()\n",
        encoding="utf-8",
    )
    (root / "pkg" / "service.py").write_text(
        "class PyService:\n    def run(self):\n        return 'ok'\n",
        encoding="utf-8",
    )

    (root / "go" / "go.mod").write_text("module example.com/polyglot\n\ngo 1.22\n", encoding="utf-8")
    (root / "go" / "main.go").write_text(
        'package main\n\nimport "example.com/polyglot/pkg"\n\nfunc main() {\n\tpkg.Run()\n}\n',
        encoding="utf-8",
    )
    (root / "go" / "pkg" / "service.go").write_text(
        'package pkg\n\nfunc Run() string {\n\treturn "ok"\n}\n',
        encoding="utf-8",
    )

    java_root = root / "java" / "src" / "main" / "java" / "com" / "example"
    (java_root / "App.java").write_text(
        "package com.example;\n\nimport com.example.Service;\n\npublic class App {\n  public String run() { return new Service().run(); }\n}\n",
        encoding="utf-8",
    )
    (java_root / "Service.java").write_text(
        "package com.example;\n\npublic class Service {\n  public String run() { return \"ok\"; }\n}\n",
        encoding="utf-8",
    )


def _all_symbols(files: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for payload in files.values():
        if not isinstance(payload, dict):
            continue
        for symbol in payload.get("symbols") or []:
            if isinstance(symbol, dict) and symbol.get("name"):
                names.add(str(symbol["name"]))
    return names


def _project_files(atlas: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(project_key): project_payload.get("files", {})
        for project_key, project_payload in atlas.items()
        if isinstance(project_payload, dict)
        and isinstance(project_payload.get("files"), dict)
    }


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="nexora_external_polyglot_smoke_") as tmp:
        target_root = Path(tmp)
        _write_fixture(target_root)
        env = dict(os.environ)
        env["CODEMAPS_TARGET_ROOT"] = str(target_root)
        env["CODEMAPS_TOPOLOGY_MODE"] = "multi_project"

        proc = _run([sys.executable, "-m", "tools.engines.generate_atlas"], env)
        checks.append(
            {
                "name": "external_polyglot_atlas_command",
                "passed": proc.returncode == 0,
                "details": {"returncode": proc.returncode, "stderr": (proc.stderr or "")[-500:]},
            }
        )

        prefix = _safe_prefix(target_root.name)
        candidates = sorted((CODE_MAPS_DIR / "output" / "external_targets").glob(f"{prefix}_*/.raw/atlas.json"))
        atlas_path = candidates[-1] if candidates else None
        atlas = json.loads(atlas_path.read_text(encoding="utf-8")) if atlas_path and atlas_path.exists() else {}
        projects = _project_files(atlas if isinstance(atlas, dict) else {})
        main_files = projects.get("MAIN", {})
        go_files = projects.get("GO", {})
        project_file_keys = {
            project_key: set(files)
            for project_key, files in projects.items()
        }
        symbols = {
            symbol
            for files in projects.values()
            for symbol in _all_symbols(files)
        }

        checks.append(
            {
                "name": "external_polyglot_indexes_python_go_java_files",
                "passed": (
                    {
                        "app.py",
                        "pkg/service.py",
                        "java/src/main/java/com/example/App.java",
                    }.issubset(project_file_keys.get("MAIN", set()))
                    and {"main.go", "pkg/service.go"}.issubset(
                        project_file_keys.get("GO", set())
                    )
                ),
                "details": {
                    "topology_mode": env["CODEMAPS_TOPOLOGY_MODE"],
                    "project_file_counts": {
                        key: len(value)
                        for key, value in sorted(project_file_keys.items())
                    },
                    "project_file_samples": {
                        key: sorted(value)[:12]
                        for key, value in sorted(project_file_keys.items())
                    },
                },
            }
        )
        checks.append(
            {
                "name": "external_polyglot_preserves_nested_project_ownership",
                "passed": (
                    "GO" in projects
                    and not any(path.startswith("go/") for path in main_files)
                    and {"main.go", "pkg/service.go"}.issubset(set(go_files))
                ),
                "details": {
                    "projects": sorted(projects),
                    "main_contains_go_prefix": any(
                        path.startswith("go/") for path in main_files
                    ),
                    "go_files": sorted(go_files),
                },
            }
        )
        checks.append(
            {
                "name": "external_polyglot_extracts_structural_symbols",
                "passed": {"App", "PyService", "Run"}.intersection(symbols) and "Service" in symbols,
                "details": {"symbols": sorted(symbols)[:30]},
            }
        )
        py_imports = main_files.get("app.py", {}).get("imports", [])
        checks.append(
            {
                "name": "external_polyglot_resolves_python_imports",
                "passed": "pkg/service.py" in py_imports,
                "details": {"app_imports": py_imports},
            }
        )

    retention_result = prune_generated_external_target_fixtures(
        keep_per_prefix=DEFAULT_KEEP_PER_PREFIX,
        dry_run=False,
    )
    checks.append(
        {
            "name": "external_polyglot_generated_fixture_retention_applied",
            "passed": retention_result.get("status") == "PASS",
            "details": {
                "keep_per_prefix": retention_result.get("keep_per_prefix"),
                "selected_for_removal": retention_result.get("selected_for_removal"),
                "failed": retention_result.get("failed", []),
            },
        }
    )

    payload = {
        "meta": {"kind": "external_polyglot_smoke_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "external_polyglot_smoke_validation.json", payload)
    lines = [
        "# External Polyglot Smoke Validation",
        "",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for check in checks:
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} |")
    save_text_atomic(REPORTS_DIR / "external_polyglot_smoke_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
