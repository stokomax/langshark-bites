# observability

Tracing for LangChain / LangGraph apps, with Phoenix (OpenInference) as the first backend. The package is structured as a namespace so other backends (e.g. LangSmith) can be added later without changing the public API.

## The problem

As your multi-agent app grows, you need to see *what* the supervisor delegated, *which* sub-agent ran, *what tools* it called, and *why* a run was slow or failed. Phoenix is a self-hosted tracing solution you can run locally to debug and tune a single agent process or a full deployment. Adding the full Phoenix SDK directly to an agent process, however, introduces a dependency conflict described below.

### Dependency footprint of the full `arize-phoenix` SDK

The full `arize-phoenix` app SDK has a large dependency footprint. It pulls in a chain of packages — `pydantic-ai-slim` → `genai-prices` → `httpx2` — that can **conflict with a LangChain app running `openai>=2.53`**.

The root cause is an **import-ordering race on the `httpx2` module**:

- `openai>=2.53` reads `sys.modules["httpx2"]` and assumes it is fully initialized.
- If Phoenix's SDK import races a concurrent `ChatOpenAI` / `ChatDeepSeek` construction, `httpx2` can be *partially initialized* at the moment `openai` reads it.

The result is a crash like:

```
AttributeError: partially initialized module 'httpx2' ... no attribute 'URL'
```

Because the crash depends on import timing, it is not deterministic and appears only under concurrent load. The full SDK also enlarges the dependency tree and the install time, while the agent process itself does not use the Phoenix UI/collector.

### How langshark-bites avoids it

langshark-bites depends only on **`arize-phoenix-otel`**, the slim OTEL client. It provides exactly the two functions observability needs:

- `phoenix.otel.register(...)` — wires the OpenTelemetry exporter to your Phoenix collector.
- `phoenix.otel.using_attributes(metadata=...)` — attaches filterable metadata to spans.

The Phoenix UI/collector is meant to run as its **own service** (e.g. the official Phoenix Docker container), not inside your agent process. Because the agent process depends only on `arize-phoenix-otel` — which does not import the packages that pull in `httpx2` — the import-ordering conflict does not arise.

## How this bite helps

`init_phoenix` is idempotent and thread-safe: call it once per process (ideally at startup, or via `asyncio.to_thread` if you are under ASGI, since registration scans the SDK and can take seconds). If Phoenix is missing or registration fails, it **soft-fails** — tracing silently no-ops, so a registration failure does not stop the application.

The span decorators — `agent_span`, `chain_span`, `tool_span` — are the Phoenix equivalent of LangSmith's `@traceable`. They are thin wrappers over OpenInference that:

- **No-op gracefully** when Phoenix is not initialized (safe in unit tests).
- Tag spans with the correct **OpenInference span kind** so they render correctly in the Phoenix UI.
- Attach **metadata** via `metadata_fn` so spans carry filterable keys.
- Resolve span names from **bound parameters** (see below).

## The span types

OpenInference distinguishes span kinds to structure the trace tree in the Phoenix UI. langshark-bites exposes one decorator per kind:

### `agent_span` — supervisors and sub-agents

Use this for any node that represents an **agent**: both the *supervisor* that routes work and the *sub-agents/workers* it delegates to. In a supervisor/worker graph:

- The **supervisor** span wraps the routing decision — `@agent_span(name="supervisor")`.
- Each **worker** span wraps one sub-agent's execution — `@agent_span(name="agent_name")`.

Because `name` can be a parameter name, the span is named after the *runtime* value, not the wrapper function. This is the mechanism that gives each worker span the correct name:

```python
@agent_span(name="agent_name", metadata_fn=lambda agent_name, as_of, **_: {
    "agent": agent_name, "as_of": as_of,
})
async def run_worker(agent_name: str, as_of: str, ...):
    ...
```

Here `name="agent_name"` resolves to the actual agent being run (e.g. `"daily_signal_analysis"`), so the Phoenix trace shows *which* worker ran — even though the function is invoked positionally in a loop.

### `chain_span` — deterministic routing / sequences

Use for a **non-agent** step that coordinates a sequence of calls — e.g. a router that picks a path, or a brief generator that orchestrates several LLM calls. It is lighter-weight than an agent span and signals "this is orchestration, not a sub-agent."

### `tool_span` — tool/callable invocations

Use for individual **tool calls** the agent makes (search, DB queries, API calls). This gives you a leaf-level view of what each agent actually did, and lets you spot slow or failing tools.

## What topologies it supports

- Supervisor/worker multi-agent graphs (distinguish supervisor vs. worker agents).
- Any node that wants a span: chains, tools, retrieval steps.
- Works alongside `langchain-core`'s LangGraph runtime; with `auto_instrument=True`, LangChain's own LLM/tool calls nest under your manual spans automatically.


## Example

See [examples/observability.py](https://github.com/stokomax/langshark-bites/blob/main/examples/observability.py) for a runnable example of this bite. Run it with:

```bash
uv run python examples/observability.py
```

## API reference

::: langshark_bites.observability
    options:
      show_submodules: true
