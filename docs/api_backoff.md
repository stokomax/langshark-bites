# api_backoff

Observable backoff sleep helper for retrying throttled external API calls.

## The problem

When a request is throttled, the API provider usually tells you how long to wait — for example, an HTTP `429` response carries a `Retry-After` header, or you back off for a known interval. You need to sleep that exact amount before retrying. A plain [`asyncio.sleep`](https://docs.python.org/3/library/asyncio-task.html#asyncio.sleep) works, but operators cannot see that a service is being throttled, or for how long. When something goes wrong in production, you want the logs to tell you which service is throttled and how long the wait is.

## How this bite helps

You determine the wait time — scraped from the provider's `Retry-After` header, or computed from an exponential backoff or the rate limiter's refill estimate — and pass it to `async_backoff`. It sleeps that exact time and **logs a WARNING only when the wait exceeds a configurable threshold**, so a long or throttled stall shows up in structured logs instead of an invisible silence. You also pass a short context string so the log line says which service and which retry.

```python
from langshark_bites.api_backoff import async_backoff, retry_after_seconds

# Provider said to wait 30s (Retry-After header); fall back to 30s if absent:
wait = retry_after_seconds(response) or 30.0
await async_backoff(wait, context="NewsAPI retry 1/3")
```

`async_backoff` is a drop-in replacement for [`asyncio.sleep`](https://docs.python.org/3/library/asyncio-task.html#asyncio.sleep): anywhere you currently `await asyncio.sleep(wait)` before a retry, you can call `await async_backoff(wait, context=...)` instead and get the observability for free.

## What topologies it supports

- Any node that retries a throttled API call.
- Works alongside `api_rate_limiter` for the residual cases where you still get throttled.
- Standalone in any async retry loop.


## Example

See [examples/backoff.py](https://github.com/stokomax/langshark-bites/blob/main/examples/backoff.py) for a runnable example of this bite. Run it with:

```bash
uv run python examples/backoff.py
```

## API reference

::: langshark_bites.api_backoff
