"""Runnable example for the api_backoff bite.

This example shows how to use async_backoff as a drop-in replacement for
asyncio.sleep when retrying a throttled request. It logs a warning when the
delay exceeds the threshold, so operators can see the throttling.

Run with:

    uv run python examples/backoff.py

See the docs: https://stokomax.github.io/langshark-bites/api_backoff/
"""

from __future__ import annotations

import asyncio

from langshark_bites.api_backoff import async_backoff


async def fetch_with_retry() -> str:
    """Simulate a request that gets throttled and needs a retry."""
    attempts = 0
    while True:
        attempts += 1
        # Simulate a 429 response on the first two attempts.
        if attempts < 3:
            wait = 0.5
            print(f"attempt {attempts}: throttled, waiting {wait}s")
            # The context string shows up in the log line so operators know
            # which service and which retry is being throttled.
            await async_backoff(wait, context=f"NewsAPI retry {attempts}/3")
            continue
        return "ok"


async def main() -> None:
    result = await fetch_with_retry()
    print(f"final result: {result}")

    # A long delay above the default 10s threshold logs a warning. Here we
    # lower the threshold to 0.1s so the example shows the warning quickly.
    print("long backoff (logs a warning):")
    await async_backoff(0.2, context="example long wait", warning_threshold_sec=0.1)


if __name__ == "__main__":
    asyncio.run(main())
