# api_backoff

Observable backoff sleep helper for retrying throttled external API calls.

## The problem

When a request is throttled, you need to wait before retrying. A plain `asyncio.sleep` works, but operators cannot see that a service is being throttled, or for how long. When something goes wrong in production, you want the logs to tell you which service is throttled and how long the wait is.

## How this bite helps

`async_backoff` is a drop-in replacement for `asyncio.sleep` that logs a warning when the delay exceeds a configurable threshold. You pass a short context string so the log line says which service and which retry.

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
