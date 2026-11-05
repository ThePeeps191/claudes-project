"""Token prices used for budget enforcement (USD per million tokens).

These are list prices for the Claude API at the time of writing. Override or
extend them in company.toml under ``[pricing."<model-id>"]`` if they change.
"""

from __future__ import annotations

# model: (input, output, cache_read)
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-1": (10.00, 50.00, 0.25),
    "claude-fable-5": (10.00, 50.00, 1.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute cache writes
FALLBACK = PRICES["claude-opus-5-5"]


def price(model: str, overrides: dict | None = None) -> tuple[float, float, float]:
    if overrides and model in overrides:
        o = overrides[model]
        return float(o["input"]), float(o["output"]), float(o.get("cache_read", o["input"] * 0.1))
    return PRICES.get(model, FALLBACK)


def cost(model: str, usage: dict, overrides: dict | None = None) -> float:
    inp, out, cache_read = price(model, overrides)
    return (
        usage.get("input", 0) * inp
        + usage.get("output", 0) * out
        + usage.get("cache_read", 0) * cache_read
        + usage.get("cache_write", 0) * inp * CACHE_WRITE_MULTIPLIER
    ) / 1_000_000
