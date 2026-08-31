from __future__ import annotations


def strip_current_directory_prefix(path_text: str) -> str:
    """Remove explicit ./ segments without changing ../ or dot-prefixed names."""
    value = str(path_text or "")
    while value.startswith("./"):
        value = value[2:]
    return value
