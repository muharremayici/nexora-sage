from __future__ import annotations

import ast
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


AGENT_SURFACE_CONTRACT_PATH = CODE_MAPS_DIR / "config" / "agent_surface_contract.json"
SURFACE_INVENTORY_CONTRACT_PATH = CODE_MAPS_DIR / "config" / "nexora_surface_inventory_contract.json"


def _surface_inventory_contract() -> dict[str, Any]:
    payload = load_json_file(SURFACE_INVENTORY_CONTRACT_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _required_agent_tools() -> list[str]:
    payload = load_json_file(AGENT_SURFACE_CONTRACT_PATH, {})
    tools = payload.get("required_agent_tools", []) if isinstance(payload, dict) else []
    return sorted({str(item).strip() for item in tools if str(item).strip()}) if isinstance(tools, list) else []


def _core_artifacts() -> list[dict[str, Any]]:
    rows = _surface_inventory_contract().get("core_artifacts", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _capability_groups() -> list[dict[str, Any]]:
    rows = _surface_inventory_contract().get("capability_groups", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: Path) -> str:
    try:
        return path.relative_to(CODE_MAPS_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        base = _decorator_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _mcp_tools() -> list[str]:
    server_path = CODE_MAPS_DIR / "tools" / "mcp" / "server.py"
    tree = ast.parse(server_path.read_text(encoding="utf-8"))
    tools: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        decorators = {_decorator_name(decorator) for decorator in node.decorator_list}
        if "mcp.tool" in decorators:
            tools.append(node.name)
    return sorted(set(tools))


def _cli_commands() -> list[dict[str, str]]:
    import codemaps

    parser = codemaps.build_parser()
    subparsers = next(action for action in parser._actions if getattr(action, "choices", None))
    rows = []
    for name, subparser in sorted(subparsers.choices.items()):
        rows.append({"command": name, "help": subparser.description or subparser.format_usage().strip()})
    return rows


def _artifact_rows() -> list[dict[str, Any]]:
    rows = []
    for artifact in _core_artifacts():
        artifact_id = str(artifact.get("id") or "")
        raw = CODE_MAPS_DIR / str(artifact.get("raw") or "")
        report = CODE_MAPS_DIR / str(artifact.get("report") or "")
        required = bool(artifact.get("required"))
        rows.append(
            {
                "id": artifact_id,
                "required": required,
                "raw": _rel(raw),
                "raw_exists": raw.exists(),
                "raw_sha256": _sha256(raw),
                "report": _rel(report),
                "report_exists": report.exists(),
                "report_sha256": _sha256(report),
            }
        )
    return rows


def _surface_taxonomy() -> dict[str, Any]:
    return load_json_file(CODE_MAPS_DIR / "config" / "agent_surface_taxonomy.json", {})


def build_inventory() -> dict[str, Any]:
    cli_commands = _cli_commands()
    mcp_tools = _mcp_tools()
    required_mcp = _required_agent_tools()
    missing_required_mcp = sorted(set(required_mcp) - set(mcp_tools))
    artifacts = _artifact_rows()
    missing_required_artifacts = [
        row["id"]
        for row in artifacts
        if row.get("required") and not (row["raw_exists"] and row["report_exists"])
    ]
    missing_optional_artifacts = [
        row["id"]
        for row in artifacts
        if not row.get("required") and not (row["raw_exists"] and row["report_exists"])
    ]
    return {
        "meta": {
            "kind": "nexora_surface_inventory",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_nexora_surface_inventory",
            "workspace_root": str(CODE_MAPS_DIR),
        },
        "summary": {
            "cli_commands": len(cli_commands),
            "mcp_tools": len(mcp_tools),
            "required_agent_mcp_tools": len(required_mcp),
            "missing_required_mcp_tools": missing_required_mcp,
            "tracked_artifacts": len(artifacts),
            "missing_required_artifact_pairs": missing_required_artifacts,
            "missing_optional_artifact_pairs": missing_optional_artifacts,
            "status": "PASS" if not missing_required_mcp and not missing_required_artifacts else "ATTENTION",
        },
        "capability_groups": _capability_groups(),
        "surface_taxonomy": _surface_taxonomy(),
        "cli_commands": cli_commands,
        "mcp_tools": mcp_tools,
        "required_agent_mcp_tools": required_mcp,
        "artifacts": artifacts,
    }


def render_report(inventory: dict[str, Any]) -> str:
    summary = inventory.get("summary", {})
    lines = [
        "# SAGE Surface Inventory",
        "",
        f"- generated_at: `{inventory.get('meta', {}).get('generated_at')}`",
        f"- status: `{summary.get('status')}`",
        f"- cli_commands: `{summary.get('cli_commands')}`",
        f"- mcp_tools: `{summary.get('mcp_tools')}`",
        f"- required_agent_mcp_tools: `{summary.get('required_agent_mcp_tools')}`",
        f"- missing_required_mcp_tools: `{summary.get('missing_required_mcp_tools')}`",
        f"- tracked_artifacts: `{summary.get('tracked_artifacts')}`",
        f"- missing_required_artifact_pairs: `{summary.get('missing_required_artifact_pairs')}`",
        f"- missing_optional_artifact_pairs: `{summary.get('missing_optional_artifact_pairs')}`",
        "",
        "## Capability Groups",
        "",
        "| Capability | Purpose | CLI | MCP | Artifacts |",
        "|---|---|---|---|---|",
    ]
    for group in inventory.get("capability_groups", []):
        cli = "<br>".join(f"`{item}`" for item in group.get("cli", []))
        mcp = "<br>".join(f"`{item}`" for item in group.get("mcp", []))
        artifacts = "<br>".join(f"`{item}`" for item in group.get("artifacts", []))
        lines.append(f"| `{group.get('id')}` | {group.get('purpose')} | {cli} | {mcp} | {artifacts} |")

    taxonomy = inventory.get("surface_taxonomy") if isinstance(inventory.get("surface_taxonomy"), dict) else {}
    lines.extend(["", "## Agent Surface Taxonomy", ""])
    lines.append("| ID | Label | Purpose | Default |")
    lines.append("|---|---|---|---|")
    for row in taxonomy.get("actor_surfaces", []) if isinstance(taxonomy, dict) else []:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"| `{row.get('id')}` | {row.get('label')} | {row.get('purpose')} | `{row.get('default_surface')}` |"
        )
    lines.extend(["", "## Target Scopes", ""])
    lines.append("| ID | Label | Agent Usage |")
    lines.append("|---|---|---|")
    for row in taxonomy.get("target_scopes", []) if isinstance(taxonomy, dict) else []:
        if not isinstance(row, dict):
            continue
        lines.append(f"| `{row.get('id')}` | {row.get('label')} | {row.get('agent_usage')} |")

    lines.extend(["", "## CLI Commands", "", "| Command | Help |", "|---|---|"])
    for command in inventory.get("cli_commands", []):
        help_text = str(command.get("help") or "").replace("\n", " ")
        lines.append(f"| `{command.get('command')}` | {help_text} |")

    lines.extend(["", "## Required Agent MCP Tools", ""])
    for tool in inventory.get("required_agent_mcp_tools", []):
        lines.append(f"- `{tool}`")

    lines.extend(["", "## Tracked Artifacts", "", "| ID | Required | Raw | Raw Exists | Report | Report Exists |", "|---|---|---|---|---|---|"])
    for row in inventory.get("artifacts", []):
        lines.append(
            f"| `{row.get('id')}` | `{row.get('required')}` | `{row.get('raw')}` | `{row.get('raw_exists')}` | `{row.get('report')}` | `{row.get('report_exists')}` |"
        )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    inventory = build_inventory()
    save_json_atomic(RAW_DIR / "nexora_surface_inventory.json", inventory)
    save_text_atomic(REPORTS_DIR / "nexora_surface_inventory.md", render_report(inventory))
    return inventory


def main() -> int:
    inventory = run()
    print(inventory["summary"])
    return 0 if inventory["summary"].get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
