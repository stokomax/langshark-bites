"""Runnable example for the observability bite (Phoenix / OpenInference).

This example shows how to wire Phoenix tracing into a LangChain / LangGraph
app without dragging in the full ``arize-phoenix`` SDK.  It demonstrates:

- ``init_phoenix`` — idempotent, thread-safe tracer registration.
- ``agent_span`` / ``chain_span`` / ``tool_span`` — OpenInference span
  decorators that no-op gracefully when Phoenix is not initialized.

Run with:

    uv run python examples/observability.py

If a Phoenix collector is not reachable (or ``arize-phoenix-otel`` is not
installed), ``init_phoenix`` soft-fails and the decorators silently no-op —
the example still runs and shows the fallback behaviour.

See the docs: https://stokomax.github.io/langshark-bites/observability/
"""

from __future__ import annotations

import asyncio

from langshark_bites.observability import (
    agent_span,
    chain_span,
    init_phoenix,
    is_initialized,
    tool_span,
)


@agent_span(
    name="agent_name",
    metadata_fn=lambda agent_name, as_of, **_: {"agent": agent_name, "as_of": as_of},
)
async def run_worker(agent_name: str, as_of: str) -> str:
    """Simulate a sub-agent worker in a supervisor/worker graph."""
    # The span is named after `agent_name` at runtime, so the trace shows
    # which worker ran even though this is invoked positionally in a loop.
    await asyncio.sleep(0.01)
    return f"{agent_name}:{as_of}"


@chain_span(name="router")
def route(task: str) -> str:
    """Simulate a deterministic routing step."""
    return f"routed:{task}"


@tool_span(name="lookup")
def lookup(key: str) -> str:
    """Simulate a tool call."""
    return f"value:{key}"


async def main() -> None:
    # Register Phoenix once per process.  Idempotent and thread-safe.
    # Under ASGI, call this via asyncio.to_thread / at startup instead of on
    # the event loop (registration scans the SDK and can take seconds).
    init_phoenix(endpoint="http://localhost:6006", project_name="example")

    print(f"phoenix initialized: {is_initialized()}")
    print(route("generate_brief"))
    print(lookup("ticker"))

    workers = await asyncio.gather(
        run_worker("daily_signal_analysis", "2026-07-10"),
        run_worker("macro_analysis", "2026-07-10"),
    )
    print(workers)


if __name__ == "__main__":
    asyncio.run(main())
