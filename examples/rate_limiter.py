"""Runnable example for the api_rate_limiter bite.

This example shows how to configure a provider and wrap an async function
with rate limiting. It runs without a real Redis or a real external API:
the limiter falls back to an in-process token bucket when Redis is
unreachable, and the "API call" is a stand-in that just sleeps.

Run with:

    uv run python examples/rate_limiter.py

See the docs: https://stokomax.github.io/langshark-bites/api_rate_limiter/
"""

from __future__ import annotations

import asyncio

from langshark_bites.api_rate_limiter import RateLimitConfig, RateLimiter, rate_limited


async def fetch_news(ticker: str) -> str:
    """Stand-in for a real external API call."""
    await asyncio.sleep(0.01)
    return f"news for {ticker}"


async def main() -> None:
    # Build a limiter with one provider configured inline. In a real project
    # you would use RateLimiter.from_env() and a YAML file (see
    # examples/rate_limits.example.yaml).
    configs = {
        "newsapi": RateLimitConfig.from_rpm(
            "newsapi",
            requests_per_minute=60,
            burst=10,
        )
    }
    limiter = RateLimiter(
        redis_url="redis://localhost:6379/0",
        configs=configs,
    )
    # Skip the Redis connection attempt so the example runs cleanly without
    # a Redis server. The limiter falls back to an in-process token bucket.
    limiter._redis = object()  # non-None sentinel: _ensure_redis short-circuits
    limiter._redis_ok = False

    # Wrap the function with the decorator.
    limited_fetch = rate_limited(limiter, provider="newsapi")(fetch_news)

    # Fire several calls in parallel. The limiter keeps them under the
    # configured rate.
    results = await asyncio.gather(
        *(limited_fetch(t) for t in ["AAPL", "MSFT", "GOOG", "AMZN", "TSLA"])
    )
    for r in results:
        print(r)

    # You can also use the context manager directly.
    async with limiter.acquire("newsapi"):
        print(await fetch_news("NVDA"))


if __name__ == "__main__":
    asyncio.run(main())
