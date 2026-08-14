# langshark-bites

A collection of bite-size add-ons and wrappers for building durable and scalable LangChain multi-agent solutions.

Each bite solves one specific problem you run into when building multi-agent systems that call external APIs and LLM providers. They are small, reusable, and project-agnostic. You can use them together or on their own.

**Documentation:** [https://stokomax.github.io/langshark-bites/](https://stokomax.github.io/langshark-bites/)

## The bites at a glance

| Bite | Problem it solves | Key API |
|---|---|---|
| `api_rate_limiter` | Multiple replicas or subagents can exceed an external API's rate limit | `RateLimiter`, `rate_limited` |
| `api_backoff` | Retries after a throttled request are invisible to operators | `async_backoff` |
| `provider_failover` | An LLM provider's credit runs out mid-run and wastes calls | `model_with_fallbacks` |
| `json_output_parser` | Models that reject `response_format` return free-text JSON | `extract_structured_from_messages` |
| `state_reducers` | Parallel workers duplicate rows when merging into graph state | `envelope_reducer` |
| `observability` | You can't see what a supervisor delegated or why a run was slow | `init_phoenix`, `agent_span`, `chain_span`, `tool_span` |

## Installation

```bash
uv add langshark-bites
# or
pip install langshark-bites
```

## The bites

### `api_rate_limiter`

**The problem.** When several Agent Server replicas, or several parallel subagents, call the same external API, a per-process semaphore is not enough. Each process gets its own budget, so your effective limit becomes the configured limit times the number of replicas. You exceed the provider's cap and get throttled.

**How this bite helps.** It keeps the token bucket state in Redis, shared across every process, and updates it atomically with a Lua script. Callers across different workers never race on read-modify-write. If Redis is unreachable, it falls back to an in-process semaphore so the system stays up.

```python
from langshark_bites.api_rate_limiter import RateLimiter, rate_limited

limiter = RateLimiter.from_env()

@rate_limited(limiter, provider="newsapi")
async def fetch_news(ticker: str):
    ...
```

See [examples/rate_limiter.py](examples/rate_limiter.py) for a runnable example. Configure providers in a YAML file; see [examples/rate_limits.example.yaml](examples/rate_limits.example.yaml) for the schema.

### `api_backoff`

**The problem.** When a request is throttled, you need to wait before retrying. A plain `asyncio.sleep` works, but operators cannot see that a service is being throttled, or for how long.

**How this bite helps.** `async_backoff` is a drop-in replacement for `asyncio.sleep` that logs a warning when the delay exceeds a configurable threshold. You pass a short context string so the log line says which service and which retry.

```python
from langshark_bites.api_backoff import async_backoff

await async_backoff(wait, context="NewsAPI retry 1/3")
```

See [examples/backoff.py](examples/backoff.py) for a runnable example.

### `provider_failover`

**The problem.** An LLM provider can return a permanent credit or billing error (for example, "credit balance is too low"). If you keep calling that provider, every call wastes a round trip. You want to skip it and use a fallback model instead.

**How this bite helps.** It keeps a process-level registry of exhausted providers. When a provider is marked exhausted, every subsequent model build for that provider skips it immediately and promotes the first available fallback. `model_with_fallbacks` builds a LangChain `RunnableWithFallbacks` with the circuit-breaker guard baked in.

```python
from langshark_bites.provider_failover import model_with_fallbacks

model = model_with_fallbacks(
    "claude-sonnet-4-5",
    "deepseek-v4-flash,gpt-4o-mini",
    max_tokens=8192,
    model_builder=create_model,
)
```

See [examples/provider_failover.py](examples/provider_failover.py) for a runnable example.

### `json_output_parser`

**The problem.** When you use `create_agent(response_format=...)`, the model returns validated Pydantic models. But some models, such as DeepSeek in reasoning mode, reject all forms of `response_format`. They output free-text JSON in the message content instead.

**How this bite helps.** `extract_structured_from_messages` scans the last AI message for JSON, repairs malformed or truncated JSON, and validates it against your Pydantic schema. It handles reasoning noise, markdown code fences, and token-limit truncation.

```python
from langshark_bites.json_output_parser import extract_structured_from_messages

content = state.get("structured_response")
if content is None:
    content = extract_structured_from_messages(state.get("messages", []), MySchema)
```

See [examples/json_output_parser.py](examples/json_output_parser.py) for a runnable example.

### `state_reducers`

**The problem.** When `Send` fan-out dispatches parallel workers, their results merge back into the shared graph state in non-deterministic order. A plain `operator.add` duplicates rows whenever a later node updates an existing entry.

**How this bite helps.** `envelope_reducer` upserts result entries by a stable key (`envelope_id`, falling back to `worker:as_of`). Updates merge in place instead of appending duplicates.

```python
from typing import Annotated
from langshark_bites.state_reducers import envelope_reducer

class State(TypedDict):
    collected_outputs: Annotated[list[dict], envelope_reducer]
```

See [examples/state_reducers.py](examples/state_reducers.py) for a runnable example.

### `observability`

**The problem.** As your multi-agent app grows, you need to see what a supervisor delegated, which sub-agent ran, what tools it called, and why a run was slow or failed. Wiring in tracing with a self-hosted Phoenix collector is the answer — but the full Phoenix app SDK drags in a dependency chain (`pydantic-ai-slim` → `genai-prices` → `httpx2`) that races `openai>=2.53` and crashes non-deterministically under concurrent load.

**How this bite helps.** It depends only on the slim `arize-phoenix-otel` client, so the import-ordering conflict never arises. `init_phoenix` is idempotent and thread-safe and **soft-fails** — if Phoenix is missing or unreachable, tracing silently no-ops instead of stopping the app. The `agent_span`, `chain_span`, and `tool_span` decorators are the Phoenix equivalent of LangSmith's `@traceable`: they no-op gracefully without Phoenix, tag spans with the right OpenInference span kind, attach filterable `tags`, and name each span after the running agent.

```python
from langshark_bites.observability import init_phoenix, agent_span

init_phoenix(endpoint="http://localhost:6006", project_name="my-app")

@agent_span(parse_agent_name=True, tags={"as_of": "2026-07-10"})
async def run_worker(agent_name: str, as_of: str, ...):
    ...
```

See [examples/observability.py](examples/observability.py) for a runnable example.

## Configuration

The rate limiter reads provider configs from a YAML file or environment variables. There are no built-in providers; you define the external APIs your agents call. See [examples/rate_limits.example.yaml](examples/rate_limits.example.yaml) for the schema and resolution order.
