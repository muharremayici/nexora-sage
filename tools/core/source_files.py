from __future__ import annotations

from pathlib import Path

from tools.core.config import SKIP_DIRS, SOURCE_EXTENSIONS
from tools.core.language_registry import non_source_compound_suffixes, non_source_template_extensions


def count_source_lines(content: str | None) -> int:
    """Return editor-visible source lines without inflating trailing-newline files."""
    if content is None:
        return 0
    text = str(content)
    if not text:
        return 0
    return len(text.splitlines())


def is_analysis_source_file(path_like: str | Path) -> bool:
    path = Path(path_like)
    lowered_parts = {part.lower() for part in path.parts}
    if any(skip_dir.lower() in lowered_parts for skip_dir in SKIP_DIRS):
        return False

    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes:
        return False

    compound_suffix = "".join(suffixes[-2:]) if len(suffixes) >= 2 else suffixes[-1]
    if compound_suffix in non_source_compound_suffixes():
        return False

    final_suffix = suffixes[-1]
    if final_suffix in non_source_template_extensions():
        return False

    return final_suffix in {ext.lower() for ext in SOURCE_EXTENSIONS}
