import unicodedata
from typing import Any

SUSPICIOUS_MARKERS = ("Ã", "Ä", "Å", "â", "ğŸ")


def _suspicious_score(text: str) -> int:
    extended_markers = (
        *SUSPICIOUS_MARKERS,
        "\u00c3",
        "\u00c4",
        "\u00c5",
        "\u00e2",
        "\u00c2",
        "\ufffd",
    )
    return sum(text.count(marker) for marker in extended_markers)


def has_suspicious_text(text: str) -> bool:
    return _suspicious_score(str(text or "")) > 0


def repair_text(text: str) -> str:
    repaired = unicodedata.normalize("NFC", text)
    for _ in range(2):
        if _suspicious_score(repaired) == 0:
            break
        candidates = [repaired]
        for source_encoding in ("latin-1", "cp1252"):
            try:
                candidates.append(repaired.encode(source_encoding).decode("utf-8"))
            except Exception:
                continue
        repaired = min(candidates, key=_suspicious_score)
        repaired = unicodedata.normalize("NFC", repaired)
    return repaired


def deep_repair(value: Any) -> Any:
    if isinstance(value, str):
        return repair_text(value)
    if isinstance(value, list):
        return [deep_repair(item) for item in value]
    if isinstance(value, dict):
        return {deep_repair(key): deep_repair(val) for key, val in value.items()}
    return value
