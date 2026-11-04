"""The tokens and API cost of one ``%%ask`` turn, in one shape for both backends.

``input`` counts every prompt token, cached or not, and ``cached`` the share
read from the cache. ``cost_usd`` is what the turn would cost at API prices;
both backends run on a subscription, so nobody is charged this amount.
"""

from typing import Any, Mapping, Optional

# USD per 1M tokens: input, cached input, output. Standard tier, short context.
# Source: https://developers.openai.com/api/docs/pricing, read 2026-11-03.
CODEX_PRICES = {
    "gpt-6-astra": (10.00, 1.00, 50.00),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6.1-terra": (1.00, 0.05, 5.00),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-5.6-sol": (4.00, 0.40, 20.00),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-4o": (2.50, 1.25, 10.00),
}


def codex_usage(total: Mapping[str, Any], model: str) -> dict[str, Any]:
    """Read a Codex ``TokenUsageBreakdown``; price it when the model is known.

    Codex counts cached tokens inside ``inputTokens`` and reasoning tokens
    inside ``outputTokens``, as the OpenAI API bills them.

    >>> codex_usage({"inputTokens": 1_000_000, "cachedInputTokens": 500_000,
    ...              "outputTokens": 100_000}, "gpt-5.6-luna")
    {'input': 1000000, 'cached': 500000, 'output': 100000, 'cost_usd': 0.23}
    >>> codex_usage({"inputTokens": 5, "cachedInputTokens": 0,
    ...              "outputTokens": 1}, "gpt-4")["cost_usd"] is None
    True
    """
    tokens = int(total.get("inputTokens", 0))
    cached = int(total.get("cachedInputTokens", 0))
    output = int(total.get("outputTokens", 0))
    price = CODEX_PRICES.get(model)
    cost: Optional[float] = None
    if price is not None:
        cost = round(
            ((tokens - cached) * price[0] + cached * price[1] + output * price[2]) / 1e6, 6
        )
    return {"input": tokens, "cached": cached, "output": output, "cost_usd": cost}


def claude_usage(result: Mapping[str, Any]) -> dict[str, Any]:
    """Read the ``result`` message Claude Code ends a turn with.

    Claude Code counts cache reads and cache writes apart from
    ``input_tokens``, and prices the turn itself in ``total_cost_usd``.

    >>> claude_usage({"total_cost_usd": 0.042, "usage": {"input_tokens": 10,
    ...     "cache_read_input_tokens": 900, "cache_creation_input_tokens": 90,
    ...     "output_tokens": 50}})
    {'input': 1000, 'cached': 900, 'output': 50, 'cost_usd': 0.042}
    """
    usage = result.get("usage") or {}
    cached = int(usage.get("cache_read_input_tokens", 0))
    tokens = (
        int(usage.get("input_tokens", 0))
        + cached
        + int(usage.get("cache_creation_input_tokens", 0))
    )
    cost = result.get("total_cost_usd")
    return {
        "input": tokens,
        "cached": cached,
        "output": int(usage.get("output_tokens", 0)),
        "cost_usd": None if cost is None else float(cost),
    }
