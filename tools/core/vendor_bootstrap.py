import os
import site
import sys
from pathlib import Path


def inject_vendor_paths(code_maps_dir: Path) -> list[Path]:
    runtime_vendor_dir = code_maps_dir / "vendor_runtime"
    workspace_vendor_dir = code_maps_dir.parent / "codemaps_vendor"
    local_vendor_dir = code_maps_dir / ".vendor"
    local_vendor_user_dir = code_maps_dir / ".vendor_user"
    candidates = [runtime_vendor_dir, workspace_vendor_dir, local_vendor_dir, local_vendor_user_dir]

    readable_vendors: list[Path] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            os.listdir(path)
        except PermissionError:
            # Skip locked vendor roots instead of poisoning runtime import paths.
            continue
        readable_vendors.append(path)

    for vendor_dir in reversed(readable_vendors):
        vendor_str = str(vendor_dir)
        if vendor_str not in sys.path:
            sys.path.insert(0, vendor_str)

    user_site = Path(site.getusersitepackages())
    if user_site.exists():
        user_site_str = str(user_site)
        if user_site_str not in sys.path:
            sys.path.insert(0, user_site_str)

    return readable_vendors
