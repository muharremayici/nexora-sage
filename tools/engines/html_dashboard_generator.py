from __future__ import annotations

import html
import json
import sys
from typing import Any
from pathlib import Path

CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_text_atomic
from tools.core.json_io import load_json_file


DASHBOARD_ARTIFACTS = [
    ("React Compiler", "react_compiler_readiness"),
    ("Next/RSC Boundary", "next_boundary_analysis"),
    ("A11y/i18n", "a11y_i18n_contracts"),
    ("Merge Simulation", "merge_simulation"),
    ("Merge Cockpit", "merge_decision_cockpit"),
    ("AI Task Packs", "ai_task_packs"),
    ("Source Contracts", "source_contract_validation"),
]


def _summary(name: str) -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / f"{name}.json", {})
    return payload.get("summary", {}) if isinstance(payload, dict) else {}


def _card(title: str, artifact: str) -> str:
    summary = _summary(artifact)
    body = html.escape(json.dumps(summary, ensure_ascii=False, indent=2)[:2200])
    if not summary:
        body = "Artifact summary not found yet."
    return f"""
    <section class="card">
      <h2>{html.escape(title)}</h2>
      <p class="artifact">output/.raw/{html.escape(artifact)}.json</p>
      <pre>{body}</pre>
    </section>
    """


def generate_dashboard() -> str:
    cards = "\n".join(_card(title, artifact) for title, artifact in DASHBOARD_ARTIFACTS)
    content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Nexora SAGE Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #18202a;
      --muted: #667085;
      --line: #d7dde5;
      --panel: #ffffff;
      --bg: #f4f7fb;
      --accent: #0f766e;
      --warn: #b45309;
    }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    header {{
      padding: 28px 32px 20px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 30px;
      letter-spacing: 0;
    }}
    .sub {{
      margin: 0;
      color: var(--muted);
      max-width: 900px;
      line-height: 1.5;
    }}
    main {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
      padding: 20px 32px 36px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      min-width: 0;
    }}
    h2 {{
      margin: 0 0 4px;
      font-size: 18px;
    }}
    .artifact {{
      margin: 0 0 12px;
      color: var(--accent);
      font-size: 13px;
      overflow-wrap: anywhere;
    }}
    pre {{
      margin: 0;
      padding: 12px;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #f9fbfd;
      overflow: auto;
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Nexora SAGE</h1>
    <p class="sub">Sovereign Architectural Governance Engine snapshot for humans. AI agents should prefer the JSON artifacts in output/.raw.</p>
  </header>
  <main>
    {cards}
  </main>
</body>
</html>
"""
    out_path = REPORTS_DIR / "nexora_sage_dashboard.html"
    save_text_atomic(out_path, content)
    return str(out_path)


def main() -> int:
    path = generate_dashboard()
    print(json.dumps({"dashboard": "PASS", "path": path}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
