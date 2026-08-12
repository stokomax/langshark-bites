# langshark-bites

<div class="banner-hero">
<pre>
<span class="banner-fin">          ██</span>
<span class="banner-fin">        ████</span>
<span class="banner-fin">      ██████</span>
<span class="banner-fin">    █████████</span>
<span class="banner-wordmark">L A N G S H A R K   B I T E S</span>
</pre>
</div>


A collection of bite-size add-ons and wrappers for building durable and scalable LangChain multi-agent solutions.

## What is this?

If you are building a multi-agent system with LangChain or LangGraph, your agents spend most of their time doing two things: calling external APIs (news feeds, databases, search) and calling LLM providers. Both of those calls can fail or get throttled in ways that are hard to see and hard to control. When you scale out to more replicas or more parallel subagents, those problems get worse.

This package is a set of small, reusable building blocks that handle those frustrating infrastructure problems for you. Each block, called a "bite", solves one specific problem. You can use them together or on their own. They do not care about your business logic, so you can drop them into any LangChain or LangGraph project.

## How the bites fit together

Think of a single request your agent makes to an external service. It goes through a lifecycle, and each bite handles one stage of that lifecycle.

```
  BEFORE the call          THE CALL              AFTER rejection
  ───────────────          ─────────             ───────────────
  api_rate_limiter         provider API          api_backoff
  (acquire a token)        (may return 429)      (wait + log, then retry)
  → prevents hitting       → if throttled,       → gives the provider
    the limit                you land here         time to recover
```

The diagram above shows one lifecycle: the call to an external API. Two bites cover it — `api_rate_limiter` before the call and `api_backoff` after a rejection. Three more bites handle failure points elsewhere in the agent flow: the LLM provider itself, the model's output, and how parallel workers' results merge back into the graph state.

- **`provider_failover`** handles the case where an LLM provider is permanently down (for example, its credit ran out). It skips that provider and uses a fallback model.
- **`json_output_parser`** handles the case where a model returns free-text JSON instead of the structured output you asked for. It parses and validates it.
- **`state_reducers`** handles the case where parallel subagents return results in random order. It merges them into your graph state without duplicating rows.

If you are new to LangGraph, the [quick note on terms](#a-quick-note-on-langgraph-terms) below explains the vocabulary. If you want to see how the bites work together in one realistic scenario, see [Combining the bites](combining.md).

## A scaling journey

Most multi-agent projects start small. You build a prototype on your laptop, one process, a handful of agents. It works. Then you deploy it, run more replicas, fan out more subagents, and the infrastructure problems start. The code that worked in the prototype starts getting throttled, or duplicating results, or wasting calls to a provider that ran out of credit.

The bites are built so you do not have to rewrite your code when that happens. You write against the same API whether you are running one process or a hundred. The difference is handled by the infrastructure, not by your code.

The clearest example is `api_rate_limiter`. On your laptop, with no Redis running, it uses an in-process token bucket. Your code is unchanged. When you deploy to many replicas and point it at Redis, it coordinates the budget across all of them. Same code, same calls, just a shared budget.

The other bites follow the same idea. `provider_failover`, `json_output_parser`, and `state_reducers` behave the same at small and large scale, because they do not depend on how many processes you run. `api_backoff` is just a sleep that logs, so it is scale-agnostic by nature.

The practical result: you implement once, and the same code carries you from a single-process prototype to a multi-replica deployment. You add infrastructure as you grow, not code.

## Do you recognize these problems?

- Your agents call an external API, and once you run more than one replica, you start getting throttled. A per-process limit is not enough.
- A request gets throttled, you retry, but nobody can tell from the logs that a service is being throttled or for how long.
- An LLM provider's credit runs out in the middle of a run. Every call to that provider wastes a round trip, and you want to fall back to another model automatically.
- A model refuses to honor `response_format` and returns free-text JSON buried in reasoning noise. You need to parse it reliably.
- You fan out work to parallel subagents, and their results come back in random order. A plain list append duplicates rows when a later node updates an existing entry.
- You cannot see, from a deployed run, which sub-agent ran, what tools it called, or where the time went. You want distributed traces of your agents.

If any of these sound familiar, this package is for you.

## The bites at a glance

<div class="grid cards" markdown>

-   **`api_rate_limiter`**

    ---

    Multiple replicas or subagents can exceed an external API's rate limit.

    **Key API:** `RateLimiter`, `rate_limited`

    ```python
    from langshark_bites.api_rate_limiter import RateLimiter, rate_limited
    ```

**Example:** [rate_limiter.py](https://github.com/stokomax/langshark-bites/blob/main/examples/rate_limiter.py)

-   **`api_backoff`**

    ---

    Retries after a throttled request are invisible to operators.

    **Key API:** `async_backoff`

    ```python
    from langshark_bites.api_backoff import async_backoff
    ```

**Example:** [backoff.py](https://github.com/stokomax/langshark-bites/blob/main/examples/backoff.py)

-   **`provider_failover`**

    ---

    An LLM provider's credit runs out mid-run and wastes calls.

    **Key API:** `create_model_with_fallback`

    ```python
    from langshark_bites.provider_failover import create_model_with_fallback
    ```

**Example:** [provider_failover.py](https://github.com/stokomax/langshark-bites/blob/main/examples/provider_failover.py)

-   **`json_output_parser`**

    ---

    Models that reject `response_format` return free-text JSON.

    **Key API:** `extract_structured_from_messages`

    ```python
    from langshark_bites.json_output_parser import extract_structured_from_messages
    ```

**Example:** [json_output_parser.py](https://github.com/stokomax/langshark-bites/blob/main/examples/json_output_parser.py)

-   **`state_reducers`**

    ---

    Parallel workers duplicate rows when merging into graph state.

    **Key API:** `envelope_reducer`

    ```python
    from langshark_bites.state_reducers import envelope_reducer
    ```

**Example:** [state_reducers.py](https://github.com/stokomax/langshark-bites/blob/main/examples/state_reducers.py)

-   **`observability`**

    ---

    You cannot tell from the runtime which worker ran, what tools it called, or why a run was slow.

    **Key API:** `init_phoenix`, `agent_span`, `chain_span`, `tool_span`

    ```python
    from langshark_bites.observability import init_phoenix, agent_span
    ```

**Example:** [observability.py](https://github.com/stokomax/langshark-bites/blob/main/examples/observability.py)

</div>

## A quick note on LangGraph terms

If you are new to LangGraph, a few terms come up throughout these docs. Here is what they mean in plain language.

- **Graph state.** The shared data structure that flows through your agent graph. As nodes run, they read from and write to this state. It is how results accumulate across steps.
- **Node.** A single step in your graph. It takes the current state, does some work (often calling an API or an LLM), and returns an update to the state.
- **Reducer.** A function LangGraph calls to merge a node's update into the existing state. It decides how new values combine with old ones.
- **`Send` fan-out.** A LangGraph pattern where one node dispatches many parallel workers in a single step. All workers run at once and their results merge back into the state.
- **Superstep.** A single step in which LangGraph runs several ready nodes at once, in parallel. When `Send` dispatches many tasks, they all run in the same superstep.
- **`create_agent`.** A LangChain helper that builds an agent from a model and a set of tools.
- **Middleware.** Code that runs around each step an agent performs, and most importantly around the call to the LLM. Middleware wraps that call so it can observe or modify the request before it is sent and the response after it returns. In the agent-to-LLM flow, middleware sits between the agent and the model: it can inject a system prompt, capture the outgoing prompt, intercept tool calls, and record the model's output. Tracing and structured-output handling are typically implemented as middleware.
- **`response_format`.** A parameter that asks a model to return structured output (for example, JSON matching a schema) instead of free text.

## Installation

```bash
uv add langshark-bites
# or
pip install langshark-bites
```

## The bites

### `api_rate_limiter`

**The problem.** Your agents call external APIs. When you scale out, you run into a limit that a single-process limiter cannot handle.

Here is the situation. A per-process semaphore gives each process its own budget. If you run three replicas, your effective rate limit becomes the configured limit times three. You exceed the provider's cap and get throttled, even though each process thinks it is behaving.

The same thing happens with `Send` fan-out. When one superstep dispatches many parallel subagents, they all call the same API at once. A per-process limit does not coordinate them.

**How this bite helps.** It keeps the token bucket state in Redis, shared across every process, and updates it atomically with a Lua script. Callers across different workers never race on read-modify-write. If Redis is unreachable, it falls back to an in-process semaphore so the system stays up.

**What topologies it supports.**

- Multi-replica Agent Server deployments, where several processes call the same external API.
- `Send` fan-out, where many parallel subagents in one superstep call the same API.
- Any node that calls an external API and needs a shared budget.

```python
from langshark_bites.api_rate_limiter import RateLimiter, rate_limited

limiter = RateLimiter.from_env()

@rate_limited(limiter, provider="newsapi")
async def fetch_news(ticker: str):
    ...
```

See [examples/rate_limiter.py](https://github.com/stokomax/langshark-bites/blob/main/examples/rate_limiter.py) for a runnable example. Configure providers in a YAML file; see [examples/rate_limits.example.yaml](https://github.com/stokomax/langshark-bites/blob/main/examples/rate_limits.example.yaml) for the schema.

### `api_backoff`

**The problem.** When a request is throttled, you need to wait before retrying. A plain `asyncio.sleep` works, but operators cannot see that a service is being throttled, or for how long. When something goes wrong in production, you want the logs to tell you which service is throttled and how long the wait is.

**How this bite helps.** `async_backoff` is a drop-in replacement for `asyncio.sleep` that logs a warning when the delay exceeds a configurable threshold. You pass a short context string so the log line says which service and which retry.

**What topologies it supports.**

- Any node that retries a throttled API call.
- Works alongside `api_rate_limiter` for the residual cases where you still get throttled.
- Standalone in any async retry loop.

```python
from langshark_bites.api_backoff import async_backoff

await async_backoff(wait, context="NewsAPI retry 1/3")
```

See [examples/backoff.py](https://github.com/stokomax/langshark-bites/blob/main/examples/backoff.py) for a runnable example.

### `provider_failover`

**The problem.** An LLM provider can return a permanent credit or billing error (for example, "credit balance is too low"). If you keep calling that provider, every call wastes a round trip. You want to skip it and use a fallback model instead, automatically.

**How this bite helps.** It keeps a process-level registry of exhausted providers. When a provider is marked exhausted, every subsequent model build for that provider skips it immediately and promotes the first available fallback. `create_model_with_fallback` builds a LangChain `RunnableWithFallbacks` with the circuit-breaker guard baked in.

**What topologies it supports.**

- `create_agent` with multiple LLM providers, where you want automatic fallback when one provider fails.
- Model factories that build models from a name, where you want a guard against exhausted providers.
- Agent or model caches that need to be cleared when a provider goes down, via the `on_exhausted` callback.

```python
from langshark_bites.provider_failover import create_model_with_fallback

model = create_model_with_fallback(
    "claude-sonnet-4-5",
    "deepseek-v4-flash,gpt-4o-mini",
    max_tokens=8192,
    model_builder=create_model,
)
```

See [examples/provider_failover.py](https://github.com/stokomax/langshark-bites/blob/main/examples/provider_failover.py) for a runnable example.

### `json_output_parser`

**The problem.** When you use `create_agent(response_format=...)`, the model returns validated Pydantic models. But some models, such as DeepSeek in reasoning mode, reject all forms of `response_format`. They output free-text JSON in the message content instead, often with reasoning noise before the actual output.

**How this bite helps.** `extract_structured_from_messages` scans the last AI message for JSON, repairs malformed or truncated JSON, and validates it against your Pydantic schema. It handles reasoning noise, markdown code fences, and token-limit truncation.

**What topologies it supports.**

- `create_agent` with `response_format`, where the model may reject it.
- Any agent that needs structured output from a model that will not honor `response_format`.
- DeepSeek thinking or reasoning mode, where the model embeds JSON in its message content.

```python
from langshark_bites.json_output_parser import extract_structured_from_messages

content = state.get("structured_response")
if content is None:
    content = extract_structured_from_messages(state.get("messages", []), MySchema)
```

See [examples/json_output_parser.py](https://github.com/stokomax/langshark-bites/blob/main/examples/json_output_parser.py) for a runnable example.

### `state_reducers`

**The problem.** When `Send` fan-out dispatches parallel workers, their results merge back into the shared graph state in non-deterministic order. A plain `operator.add` duplicates rows whenever a later node updates an existing entry. For example, a persist node flips `persisted=True` on an already-collected result, and now that result appears twice.

**How this bite helps.** `envelope_reducer` upserts result entries by a stable key (`task_id`, falling back to `worker:as_of`). Updates merge in place instead of appending duplicates.

**What topologies it supports.**

- `Send` fan-out, where parallel workers accumulate results into shared state.
- Multi-agent graphs where a persist node updates already-collected results.
- Any graph state field that accumulates a list of result envelopes keyed by `task_id`.

```python
from typing import Annotated
from langshark_bites.state_reducers import envelope_reducer

class State(TypedDict):
    collected_outputs: Annotated[list[dict], envelope_reducer]
```

See [examples/state_reducers.py](https://github.com/stokomax/langshark-bites/blob/main/examples/state_reducers.py) for a runnable example.

### `observability`

**The problem.** When an agent run misbehaves, you want to see what actually happened: which worker the supervisor delegated to, what tools it called, and where the time or money went. A deployed multi-agent system hides this unless each step is traced.

**How this bite helps.** `init_phoenix` registers a Phoenix (OpenInference) tracer once per process, idempotently and with a soft-fail if Phoenix is missing. `agent_span`, `chain_span`, and `tool_span` wrap nodes and tool calls with OpenInference span kinds and attached metadata, so the trace tree shows the supervisor, each sub-agent, and each tool call.

**What topologies it supports.**

- Supervisor/worker multi-agent graphs (distinguish supervisor vs. worker agents).
- Any node or tool call that should appear in the trace.
- Self-hosted Phoenix collector, run as its own service alongside the app.

```python
from langshark_bites.observability import init_phoenix, agent_span

init_phoenix(endpoint="http://localhost:6006", project_name="my-app")

@agent_span(name="agent_name")
async def run_worker(agent_name: str):
    ...
```

See [examples/observability.py](https://github.com/stokomax/langshark-bites/blob/main/examples/observability.py) for a runnable example.

## Configuration

The rate limiter reads provider configs from a YAML file or environment variables. There are no built-in providers; you define the external APIs your agents call. See [examples/rate_limits.example.yaml](https://github.com/stokomax/langshark-bites/blob/main/examples/rate_limits.example.yaml) for the schema and resolution order.
