from __future__ import annotations

from math import ceil
from typing import Any


ESTIMATOR_ID = "deterministic_char_token_estimate_v1"
DEFAULT_AGENT_PACKET_BUDGET_TOKENS = 6000
DEFAULT_WATCHDOG_SESSION_PACKET_BUDGET_TOKENS = 3000
COMPACT_AGENT_PACKET_TOKENS = 1200
BOUNDED_AGENT_PACKET_TOKENS = 3000
HEAVY_AGENT_PACKET_TOKENS = 6000


def estimate_tokens(text: Any) -> int:
    """Return a deterministic, dependency-free token estimate for agent packets."""
    content = "" if text is None else str(text)
    if not content:
        return 0
    # Conservative enough for governance without binding SAGE to a model tokenizer.
    return max(1, ceil(len(content) / 4))


def context_budget_profile(text: Any, *, budget_tokens: int = DEFAULT_AGENT_PACKET_BUDGET_TOKENS) -> dict[str, Any]:
    estimated = estimate_tokens(text)
    budget = max(1, int(budget_tokens or DEFAULT_AGENT_PACKET_BUDGET_TOKENS))
    return {
        "estimator": ESTIMATOR_ID,
        "estimated_tokens": estimated,
        "budget_tokens": budget,
        "status": "pass" if estimated <= budget else "over_budget",
        "exact_tokenizer": False,
    }


def token_budget_class(estimated_tokens: int) -> str:
    tokens = int(estimated_tokens or 0)
    if tokens <= COMPACT_AGENT_PACKET_TOKENS:
        return "compact"
    if tokens <= BOUNDED_AGENT_PACKET_TOKENS:
        return "bounded"
    if tokens <= HEAVY_AGENT_PACKET_TOKENS:
        return "heavy"
    return "oversized"
