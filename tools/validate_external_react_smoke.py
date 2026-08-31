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

from tools.core.config import (
    RAW_DIR,
    REPORTS_DIR,
    external_target_project_relative_path,
    save_json_atomic,
    save_text_atomic,
)
from tools.external_target_preflight import build_preflight
from tools.core.agent_surface_target_visibility import is_structured_precondition_block
from tools.core.external_target_retention import DEFAULT_KEEP_PER_PREFIX, prune_generated_external_target_fixtures
from tools.core.operational_limits import external_react_smoke_timeout_seconds
from tools.core.subprocess_telemetry import run_observed_subprocess
from tools.mcp import server as mcp_server


def _safe_prefix(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")


def _run(command: list[str], env: dict[str, str]):
    result, _duration = run_observed_subprocess(
        command,
        cwd=CODE_MAPS_DIR,
        env=env,
        label="external_react_generate_atlas",
        timeout=external_react_smoke_timeout_seconds(),
        log=lambda message: print(f"[external-react-smoke] {message}", flush=True),
    )
    return result


def _write_fixture(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "nexora-external-react-smoke",
                "private": True,
                "dependencies": {
                    "@tanstack/react-query": "^5.0.0",
                    "react": "^19.0.0",
                    "react-dom": "^19.0.0",
                    "react-error-boundary": "^4.0.0",
                    "react-hook-form": "^7.0.0",
                    "react-router-dom": "^7.0.0",
                    "zod": "^3.0.0",
                    "zustand": "^5.0.0",
                },
                "devDependencies": {
                    "@testing-library/react": "^16.0.0",
                    "vite": "^6.0.0",
                    "vitest": "^3.0.0",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "vite.config.ts").write_text(
        "import { defineConfig } from 'vite';\nimport react from '@vitejs/plugin-react';\nexport default defineConfig({ plugins: [react()] });\n",
        encoding="utf-8",
    )
    (root / "src" / "App.tsx").write_text(
        "\n".join(
            [
                "import React, { Suspense, createContext, useContext } from 'react';",
                "import { ErrorBoundary } from 'react-error-boundary';",
                "import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';",
                "import { createBrowserRouter, RouterProvider } from 'react-router-dom';",
                "import { useForm } from 'react-hook-form';",
                "import { z } from 'zod';",
                "import { create } from 'zustand';",
                "",
                "const ThemeContext = createContext('light');",
                "const schema = z.object({ title: z.string() });",
                "const queryClient = new QueryClient();",
                "const useStore = create<{ count: number }>(() => ({ count: 0 }));",
                "",
                "function Dashboard() {",
                "  const theme = useContext(ThemeContext);",
                "  const form = useForm<{ title: string }>();",
                "  const count = useStore((state) => state.count);",
                "  const query = useQuery({ queryKey: ['books'], queryFn: () => fetch('/api/books').then((r) => r.json()) });",
                "  schema.safeParse(form.getValues());",
                "  return <main data-theme={theme}>{count}{query.data?.length ?? 0}</main>;",
                "}",
                "",
                "const router = createBrowserRouter([{ path: '/', element: <Dashboard />, loader: async () => null }]);",
                "",
                "export function App() {",
                "  return (",
                "    <ErrorBoundary fallback={<div>error</div>}>",
                "      <Suspense fallback={<div>loading</div>}>",
                "        <ThemeContext.Provider value=\"dark\">",
                "          <QueryClientProvider client={queryClient}>",
                "            <RouterProvider router={router} />",
                "          </QueryClientProvider>",
                "        </ThemeContext.Provider>",
                "      </Suspense>",
                "    </ErrorBoundary>",
                "  );",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "src" / "App.test.tsx").write_text(
        "import { render } from '@testing-library/react';\nimport { App } from './App';\ntest('renders', () => render(<App />));\n",
        encoding="utf-8",
    )


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="nexora_external_react_smoke_") as tmp:
        target_root = Path(tmp)
        _write_fixture(target_root)
        env = dict(os.environ)
        env["CODEMAPS_TARGET_ROOT"] = str(target_root)

        atlas_proc = _run([sys.executable, "-m", "tools.engines.generate_atlas"], env)
        checks.append(
            {
                "name": "external_react_atlas_command",
                "passed": atlas_proc.returncode == 0,
                "details": {"returncode": atlas_proc.returncode, "stderr": (atlas_proc.stderr or "")[-500:]},
            }
        )

        project_dna_proc = _run([sys.executable, "-m", "tools.engines.project_dna_profiler"], env)
        checks.append(
            {
                "name": "external_react_project_dna_command",
                "passed": project_dna_proc.returncode == 0,
                "details": {"returncode": project_dna_proc.returncode, "stderr": (project_dna_proc.stderr or "")[-500:]},
            }
        )

        react_proc = _run([sys.executable, "tools/validate_react_support.py"], env)
        checks.append(
            {
                "name": "external_react_support_command",
                "passed": react_proc.returncode == 0,
                "details": {"returncode": react_proc.returncode, "stderr": (react_proc.stderr or "")[-500:]},
            }
        )

        prefix = _safe_prefix(target_root.name)
        candidates = sorted((CODE_MAPS_DIR / "output" / "external_targets").glob(f"{prefix}_*/.raw"))
        artifact_root = candidates[-1] if candidates else None
        atlas_path = artifact_root / "atlas.json" if artifact_root else None
        matrix_path = artifact_root / "react_support_matrix.json" if artifact_root else None
        atlas = json.loads(atlas_path.read_text(encoding="utf-8")) if atlas_path and atlas_path.exists() else {}
        matrix = json.loads(matrix_path.read_text(encoding="utf-8")) if matrix_path and matrix_path.exists() else {}

        preflight = build_preflight(target_root)
        scope_projection = preflight.get("summary", {}).get("analysis_scope", {})
        app_project_path = external_target_project_relative_path(
            "src/App.tsx",
            scope_projection,
        )
        files = ((atlas.get("MAIN") or {}) if isinstance(atlas, dict) else {}).get("files", {})
        app_payload = files.get(app_project_path, {}) if isinstance(files, dict) and app_project_path else {}
        features = set(app_payload.get("features") or [])
        checks.append(
            {
                "name": "external_react_atlas_detects_core_features",
                "passed": {"ReactContext", "ReactProvider", "SuspenseBoundary", "RouterConfig"}.issubset(features),
                "details": {
                    "repository_relative_path": "src/App.tsx",
                    "project_relative_path": app_project_path,
                    "analysis_scope": scope_projection,
                    "features": sorted(features),
                },
            }
        )

        capabilities = {
            item.get("key"): item
            for item in matrix.get("capabilities", [])
            if isinstance(item, dict)
        } if isinstance(matrix, dict) else {}
        wanted = ["react_router_runtime", "context_error_suspense", "forms_validation", "tanstack_query", "zustand"]
        checks.append(
            {
                "name": "external_react_support_detects_expected_capabilities",
                "passed": all(capabilities.get(key, {}).get("present_in_repo") for key in wanted)
                and all(capabilities.get(key, {}).get("support") in {"detected", "partial"} for key in wanted),
                "details": {key: capabilities.get(key, {}) for key in wanted},
            }
        )
        surgical_brief = mcp_server.get_surgical_operation_packet(max_signals=3, target_root=str(target_root))
        try:
            surgical_payload = json.loads(surgical_brief)
        except (TypeError, ValueError):
            surgical_payload = {}
        successful_brief = (
            surgical_brief.startswith("# Repository Surgical Brief")
            and "output/.raw" not in surgical_brief
            and "SOVEREIGN_ELITE" not in surgical_brief
            and "atlas_node" not in surgical_brief
        )
        safe_precondition_block = is_structured_precondition_block(
            {"body": surgical_brief},
            expected_tool="get_surgical_operation_packet",
        )
        checks.append(
            {
                "name": "external_react_mcp_surgical_surface_respects_analysis_preconditions",
                "passed": successful_brief or safe_precondition_block,
                "details": {
                    "brief_chars": len(surgical_brief),
                    "successful_brief": successful_brief,
                    "safe_precondition_block": safe_precondition_block,
                    "response_status": surgical_payload.get("status"),
                    "blocking": surgical_payload.get("blocking"),
                    "forbidden_markers": [
                        marker
                        for marker in ("output/.raw", "SOVEREIGN_ELITE", "atlas_node")
                        if marker in surgical_brief
                    ],
                },
            }
        )
        app_brief = mcp_server.inspect_file("src/App.tsx", target_root=str(target_root))
        checks.append(
            {
                "name": "external_react_mcp_inspect_file_returns_react_target_brief",
                "passed": app_brief.startswith("# Target Inspection Brief")
                and '"src/App.tsx"' in app_brief
                and "target_files:" in app_brief
                and "ReactContext" not in app_brief
                and "atlas_node" not in app_brief
                and "SOVEREIGN_ELITE" not in app_brief,
                "details": {"brief_chars": len(app_brief)},
            }
        )

    retention_result = prune_generated_external_target_fixtures(
        keep_per_prefix=DEFAULT_KEEP_PER_PREFIX,
        dry_run=False,
    )
    checks.append(
        {
            "name": "external_react_generated_fixture_retention_applied",
            "passed": retention_result.get("status") == "PASS",
            "details": {
                "keep_per_prefix": retention_result.get("keep_per_prefix"),
                "selected_for_removal": retention_result.get("selected_for_removal"),
                "failed": retention_result.get("failed", []),
            },
        }
    )

    payload = {
        "meta": {"kind": "external_react_smoke_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "external_react_smoke_validation.json", payload)
    lines = [
        "# External React Smoke Validation",
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
    save_text_atomic(REPORTS_DIR / "external_react_smoke_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
