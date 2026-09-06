"""
Hexagonal Port-Adapter Binder
Scans atlas/genome to map Domain Interfaces (Ports) to Infrastructure Classes (Adapters).
Helps verify the purity of Hexagonal Architecture implementation.
"""

import argparse
import json
import os
import re
from pathlib import Path

from tools.core.atlas_io import load_atlas_data
from tools.core.config import RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.projects_registry import resolve_runtime_projects
from tools.core.source_snapshot_reader import load_source_text


IMPLEMENTS_RE = re.compile(r"export\s+class\s+(\w+)\s+(?:extends\s+\w+\s+)?implements\s+([^\{]+)")


def _ns(project_key, rel_path):
    if not rel_path:
        return rel_path
    return f"{project_key}::{rel_path}" if "::" not in rel_path else rel_path


def _strip_ns(path_or_ns):
    if not path_or_ns:
        return path_or_ns
    return path_or_ns.split("::", 1)[1] if "::" in path_or_ns else path_or_ns


def _discover_ports_from_symbols(rel_path, file_data, ports):
    for symbol in file_data.get("symbols", []):
        if not isinstance(symbol, dict):
            continue
        name = str(symbol.get("name", "")).strip()
        sym_type = str(symbol.get("type", "")).strip()
        if sym_type == "Interface" and name.endswith("Port"):
            ports[name] = {"file": rel_path}


def _discover_adapter_names_from_symbols(file_data):
    names = []
    adapter_map = {}
    missing_structural_contract = False
    for symbol in file_data.get("symbols", []):
        if not isinstance(symbol, dict):
            continue
        if str(symbol.get("type", "")).strip() == "Class":
            name = str(symbol.get("name", "")).strip()
            if name:
                names.append(name)
                if "implements" not in symbol:
                    missing_structural_contract = True
                adapter_map[name] = list(symbol.get("implements", []) or [])
    return names, adapter_map, missing_structural_contract


def run_hexagonal_binder(changed_files=None):
    logger.info("Running Hexagonal Port-Adapter Binder...")

    atlas = load_atlas_data()
    if not atlas:
        logger.error("[FAIL] Atlas payload not found. Cannot map ports to adapters.")
        return False

    from tools.core.config import DYNAMIC_CONFIG
    roles = DYNAMIC_CONFIG.get("project_roles", {})
    primary_key = next((k for k, v in roles.items() if v == "host" and k in atlas), None)
    if not primary_key:
        primary_key = "MAIN" if "MAIN" in atlas else (list(atlas.keys())[0] if atlas else None)

    if not primary_key:
        logger.error("[FAIL] No projects found in Atlas payload")
        return False

    main_atlas = atlas.get(primary_key, {})
    files = main_atlas.get("files", {})
    prev_results = load_json_file(RAW_DIR / "hexagonal_bindings.json", {})

    ports = {}
    adapters = {}

    target_rel_paths = set()
    if changed_files:
        target_rel_paths = {f.split("::", 1)[1] for f in changed_files if f.startswith(f"{primary_key}::")}

        for binding in prev_results.get("bound", []):
            prev_port_file = _strip_ns(binding.get("port_file"))
            if prev_port_file not in target_rel_paths:
                ports[binding["port"]] = {"file": _ns(primary_key, prev_port_file)}
            for adapter in binding["adapters"]:
                prev_adapter_file = _strip_ns(adapter.get("file"))
                if prev_adapter_file not in target_rel_paths:
                    if adapter["name"] not in adapters:
                        adapters[adapter["name"]] = {"file": _ns(primary_key, prev_adapter_file), "implements": []}
                    if binding["port"] not in adapters[adapter["name"]]["implements"]:
                        adapters[adapter["name"]]["implements"].append(binding["port"])

        for unbound in prev_results.get("unbound_ports", []):
            prev_unbound_file = _strip_ns(unbound.get("file"))
            if prev_unbound_file not in target_rel_paths:
                ports[unbound["port"]] = {"file": _ns(primary_key, prev_unbound_file)}

    files_to_iter = [(rel, files[rel]) for rel in target_rel_paths if rel in files] if changed_files else files.items()

    projects = resolve_runtime_projects(ROOT)
    main_path = projects.get(primary_key)

    for rel_path, file_data in files_to_iter:
        _discover_ports_from_symbols(rel_path, file_data, ports)
        for port_name, port_data in list(ports.items()):
            if port_data.get("file") == rel_path:
                port_data["file"] = _ns(primary_key, rel_path)

        adapter_names, adapter_implements_map, missing_structural_contract = _discover_adapter_names_from_symbols(file_data)
        if not adapter_names:
            continue

        if not main_path:
            continue
        abs_path = os.path.join(main_path, rel_path)
        if not os.path.exists(abs_path):
            continue

        needs_regex_fallback = missing_structural_contract or not file_data.get("ast_contract_version")
        content = None
        if needs_regex_fallback:
            content = load_source_text(
                primary_key,
                rel_path,
                fallback_path=Path(abs_path),
                component="hexagonal_binder",
            )

        for class_name in adapter_names:
            implemented_ports = list(adapter_implements_map.get(class_name) or [])
            if not implemented_ports and content:
                for match in IMPLEMENTS_RE.finditer(content):
                    if match.group(1) == class_name:
                        implemented_ports = [item.strip() for item in match.group(2).split(",")]
                        break
            adapters[class_name] = {"file": _ns(primary_key, rel_path), "implements": implemented_ports}
            for port_name in implemented_ports:
                if port_name not in ports:
                    ports[port_name] = {"file": "UNKNOWN"}

        for class_name in adapter_names:
            adapters.setdefault(class_name, {"file": _ns(primary_key, rel_path), "implements": []})

    bindings = []
    unbound_ports = []

    for port_name, port_data in ports.items():
        implemented_by = []
        for adapter_name, adapter_data in adapters.items():
            if port_name in adapter_data["implements"]:
                implemented_by.append({"name": adapter_name, "file": adapter_data["file"]})

        if implemented_by:
            bindings.append(
                {
                    "port": port_name,
                    "port_file": port_data["file"],
                    "port_project": port_data["file"].split("::", 1)[0] if "::" in port_data["file"] else "UNKNOWN",
                    "adapters": implemented_by,
                }
            )
        elif port_data["file"] != "UNKNOWN" and "Port" in port_name:
            unbound_ports.append(
                {
                    "port": port_name,
                    "file": port_data["file"],
                    "project": port_data["file"].split("::", 1)[0] if "::" in port_data["file"] else "UNKNOWN",
                }
            )

    results = {
        "bound": bindings,
        "unbound_ports": unbound_ports,
        "total_ports_discovered": len(ports),
        "total_adapters_discovered": len(adapters),
    }

    raw_path = RAW_DIR / "hexagonal_bindings.json"
    save_json_atomic(raw_path, results)

    md_lines = [
        "# Hexagonal Port-Adapter Bindings",
        "",
        "> Validates the Dependency Inversion principle by mapping Domain interfaces (Ports) to Infrastructure implementations (Adapters).",
        "",
        f"**Discovered Ports:** {len(ports)} | **Discovered Adapters:** {len(adapters)}",
        "",
    ]

    if bindings:
        md_lines.extend(["## Bound Ports", ""])
        for binding in bindings:
            md_lines.append(f"### `{binding['port']}`")
            md_lines.append(f"*Defined in: `{binding['port_file']}`*")
            for adapter in binding["adapters"]:
                md_lines.append(f"- **Implemented by:** `{adapter['name']}` *(`{adapter['file']}`)*")
            md_lines.append("")

    if unbound_ports:
        md_lines.extend(["## [WARN] Unbound (Orphaned) Ports", "Ports that have no registered adapter implementation.", ""])
        for unbound in unbound_ports:
            md_lines.append(f"- `{unbound['port']}` *(`{unbound['file']}`)*")

    md_path = REPORTS_DIR / "hexagonal_bindings.md"
    save_text_atomic(md_path, "\n".join(md_lines))

    logger.info(f"[OK] Hexagonal Bindings generated: {len(bindings)} resolved.")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-files", help="Comma-separated list of changed files")
    args = parser.parse_args()

    changed_list = args.changed_files.split(",") if args.changed_files else None
    run_hexagonal_binder(changed_files=changed_list)
