# langshark-bites

A collection of bite-size add-ons and wrappers for building durable and scalable LangChain multi-agent solutions.

Each bite solves one specific problem you run into when building multi-agent systems that call external APIs and LLM providers. They are small, reusable, and project-agnostic. You can use them together or on their own.

**Documentation:** [https://stokomax.github.io/langshark-bites/](https://stokomax.github.io/langshark-bites/)

**Integrating into an existing project:** see the [Integrating guide](https://stokomax.github.io/langshark-bites/integrating/) — a single reference (written for AI coding agents) covering which install you need, where each bite plugs in, and how to verify.

## The bites at a glance

| Bite | Problem it solves | Key API |
|---|---|---|
| `api_rate_limiter` | Multiple replicas or subagents can exceed an external API's rate limit | `RateLimiter`, `rate_limited` |
| `api_backoff` | Retries after a throttled request are invisible to operators | `async_backoff` |
| `provider_failover` | An LLM provider's credit runs out mid-run and wastes calls | `model_with_fallbacks` |
| `json_output_parser` | Models that reject `response_format` return free-text JSON | `extract_structured_from_messages` |
| `state_reducers` | Parallel workers duplicate rows when merging into graph state | `envelope_reducer` |
| `observability` | You can't see what a supervisor delegated or why a run was slow | `init_phoenix`, `agent_span`, `chain_span`, `tool_span` |
| `a2a_completion_notifier` | A separate subagent server never notifies the supervisor it finished | `build_push_config` (supervisor), `A2APushNotifierMiddleware` (emitter), `create_receiver_app` + `MailboxDrainMiddleware` (receiver) |

## Installation

`langshark-bites` is not published to pypi.org. Each push to `main` that bumps
the version in `pyproject.toml` publishes a [GitHub Release](https://github.com/stokomax/langshark-bites/releases)
tagged `v<version>` with the built wheel and sdist attached. Install directly
from the release asset URL — no registry auth required for a public repo:

```bash
# uv — pin to a specific release tag/version
uv add "https://github.com/stokomax/langshark-bites/releases/download/v1.0.0/langshark_bites-1.0.0-py3-none-any.whl"

# pip
pip install "https://github.com/stokomax/langshark-bites/releases/download/v1.0.0/langshark_bites-1.0.0-py3-none-any.whl"
```

Check the [releases page](https://github.com/stokomax/langshark-bites/releases) for the latest tag and asset filenames.

The base package covers every bite **except** `a2a_completion_notifier`. Its
package imports the FastAPI receiver at import time, so even using just the
emitter middleware requires the extra's dependencies:

```bash
# For the a2a_completion_notifier bite (fastapi, mcp[cli], uvicorn[standard]):
uv add "langshark-bites[a2a-notifier] @ https://github.com/stokomax/langshark-bites/releases/download/v1.0.0/langshark_bites-1.0.0-py3-none-any.whl"
```

### Which install you need

| Bite | Install command |
|---|---|
| `api_rate_limiter`, `api_backoff`, `provider_failover`, `json_output_parser`, `state_reducers`, `observability` | `uv add "https://github.com/stokomax/langshark-bites/releases/download/v1.0.0/langshark_bites-1.0.0-py3-none-any.whl"` |
| `a2a_completion_notifier` | `uv add "langshark-bites[a2a-notifier] @ https://github.com/stokomax/langshark-bites/releases/download/v1.0.0/langshark_bites-1.0.0-py3-none-any.whl"` |

## The bites

### `api_rate_limiter`

**The problem.** When several Agent Server replicas, or several parallel subagents, call the same external API, a per-process semaphore is not enough. Each process gets its own budget, so your effective limit becomes the configured limit times the number of replicas. You exceed the provider's cap and get throttled.

**How this bite helps.** It keeps the token bucket state in Redis, shared across every process, and updates it atomically with a Lua script. Callers across different workers never race on read-modify-write. If Redis is unreachable, it falls back to an in-process semaphore so the system stays up.

```python
from langshark_bites.api_rate_limiter import RateLimiter, rate_limited

limiter = RateLimiter.from_env()


@rate_limited(limiter, provider="newsapi")
async def fetch_news(ticker: str): ...
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

### `a2a_completion_notifier`

**The problem.** When you split a multi-agent system across two Agent Server deployments — a supervisor on one, subagents on another — the supervisor has no built-in way to learn when a subagent finishes. LangChain's async-subagent protocol is poll-based, and its A2A support does *not* implement the push side (`TaskPushNotificationConfig`/`SubscribeToTask` return `-32601`). Nothing emits the completion webhook, and nothing receives it.

**How this bite helps.** It provides both missing halves of A2A push notifications for a split deployment: an emitter that sends the completion webhook, and a receiver that accepts it on the supervisor side.

The **emitter** is `AgentMiddleware` for the subagent graph. On terminal run state it signs an A2A push notification (RS256 + JWKS) and POSTs it to the supervisor's registered webhook — middleware, not a tool, so it fires unconditionally on completion *and* on errors.

The **receiver** is a FastAPI process next to the supervisor. It authenticates and deduplicates the incoming notification, unseals the opaque callback token, and delivers it into the supervisor's context via the Store + drain pattern. The receiver can be packaged as an **MCP server** with diagnostic tools, and it starts and stops with the MCP client.

The packaging follows where agent-to-agent delivery is heading: a supervisor should not have to poll for results. Instead, a process receives the push notification and hands it to the supervisor over a connection the two already share. Here that means one process that listens on a local HTTP port for the A2A webhook POSTs and, on the other side, holds the LangGraph SDK connection to the supervisor's Agent Server — the same arrangement Claude Code Channels ships. A2A's own push-delivery mechanism is specified in the protocol, but the Agent Server does not implement it yet, so this bite provides it until that lands.

The receiver also exposes a fetch-only **result primitive** (`POST /a2a/result` / MCP `get_async_result`) — a sister to the async-agent framework's poll-based `check_async_task`. Because the notifier already proved the task is terminal, the supervisor fetches the full result in one on-demand call, never polluting its prompt with results it doesn't need.

```python
# Emitter (subagent side — in a dynamic graph factory)
from langshark_bites.a2a_completion_notifier.middleware import (
    build_a2a_notifier_from_config,
)
from langshark_bites.a2a_completion_notifier.push_client import PushClient
from langshark_bites.a2a_completion_notifier.signer import A2ASigner


def make_graph(config):
    notifier = build_a2a_notifier_from_config(
        config,
        signer=A2ASigner(pem, kid="subagent-1", issuer=..., audience=...),
        push_client=PushClient(),
    )
    return create_agent(model=..., tools=..., middleware=[notifier])
```

```python
# Receiver (supervisor side)
from langshark_bites.a2a_completion_notifier.receiver import create_receiver_app
from langshark_bites.a2a_completion_notifier.settings import ReceiverSettings

app = create_receiver_app(settings=ReceiverSettings.from_env())
```

## Configuration

The rate limiter reads provider configs from a YAML file or environment variables. There are no built-in providers; you define the external APIs your agents call. See [examples/rate_limits.example.yaml](examples/rate_limits.example.yaml) for the schema and resolution order.
