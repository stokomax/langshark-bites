# Integrating langshark-bites

One reference for wiring the bites into an existing LangChain / LangGraph
project — written to be read by an AI coding agent *before* it edits anything.

## For AI coding agents — read this first

- Read this page, then the bite's `docs/<bite>.md` page and its
  `examples/<bite>.py`, before writing code.
- Use **only the public API** re-exported in `src/langshark_bites/__init__.py`
  (and each bite's own `__init__.py`). Do not reach into private modules or
  attributes.
- **Ignore the authoring internals**: `skills/langshark-bites/SKILL.md` and
  `templates/bite/` are for editing the library itself, not for consuming it.
  `main.py` at the repo root is a hello-world remnant, never an entrypoint.
- Do not add dependencies. The base package covers every bite **except**
  `a2a_completion_notifier`, which needs the `[a2a-notifier]` extra.
- Preserve the project's existing retry / fallback / error-handling semantics
  unless the task explicitly changes them.
- Verify with the single gate at the bottom of this page.

## Which install you need

| Bite | Install |
|---|---|
| `api_rate_limiter`, `api_backoff`, `provider_failover`, `json_output_parser`, `state_reducers`, `observability` | `uv add langshark-bites` |
| `a2a_completion_notifier` | `uv add "langshark-bites[a2a-notifier]"` |

The `a2a-notifier` extra is required **even for just the emitter**: the
package imports the FastAPI receiver at module load.

## Decision table

| Bite | Where it plugs into your code | Verify (learn from the example) |
|---|---|---|
| `api_rate_limiter` | wrap the external API call function | `examples/rate_limiter.py` |
| `api_backoff` | replace `asyncio.sleep` in retry loops | `examples/backoff.py` |
| `provider_failover` | where the LLM model is constructed | `examples/provider_failover.py` |
| `json_output_parser` | where the model's free-text JSON arrives | `examples/json_output_parser.py` |
| `state_reducers` | the LangGraph state annotation | `examples/state_reducers.py` |
| `observability` | startup hook + the hot functions | `examples/observability.py` |
| `a2a_completion_notifier` | subagent graph factory (emitter) + FastAPI receiver next to the supervisor | `examples/a2a_completion_notifier.py` |

### `api_rate_limiter`

Share one token-bucket budget across every process and replica. The bucket
lives in Redis and is updated atomically with a Lua script; if Redis is
unreachable it falls back to an in-process bucket so the system stays up.

```python
from langshark_bites.api_rate_limiter import RateLimiter, rate_limited

limiter = RateLimiter.from_env()


@rate_limited(limiter, provider="newsapi")
async def fetch_news(ticker: str): ...
```

- **Config:** `RateLimiter.from_env()` reads `REDIS_URL` (default
  `redis://localhost:6379/0`) and provider config from
  `RATE_LIMIT_CONFIG_PATH` (YAML) or per-provider env vars. There are no
  baked-in providers — you define the ones your project needs.
- **Prerequisite:** a Redis instance for a shared budget (optional in dev —
  the in-process fallback still limits per process).

### `api_backoff`

A drop-in replacement for `asyncio.sleep` in retry loops that makes the wait
visible in the logs.

```python
from langshark_bites.api_backoff import async_backoff


async def fetch_news_with_retry(ticker: str) -> str:
    for attempt in range(1, 4):
        try:
            return await fetch_news(ticker)
        except ThrottledError:
            await async_backoff(2**attempt, context=f"newsapi retry {attempt}/3")
```

- **Config:** none — just the `context` string that lands in the log event.

### `provider_failover`

Build an LLM with a fallback chain and a process-level circuit breaker, so an
exhausted provider is skipped instead of wasting round-trips.

```python
from langshark_bites.provider_failover import model_with_fallbacks

model = model_with_fallbacks(
    "claude-sonnet-4-5",
    "deepseek-v4-flash,gpt-4o-mini",
    max_tokens=8192,
    model_builder=create_model,  # your (model_name, max_tokens) -> BaseChatModel
)
```

- **Config:** provider names, the comma-separated fallback list, and your
  `model_builder`.
- **Note:** rate-limit (429) errors are **excluded** by design — they belong
  to retry middleware, not the fallback chain. `ExhaustedProviderCallback`
  attaches to a model instance to detect credit/billing errors.

### `json_output_parser`

Extract and validate free-text JSON from models that reject
`response_format` (e.g. DeepSeek thinking mode).

```python
from langshark_bites.json_output_parser import extract_structured_from_messages

content = extract_structured_from_messages(state.get("messages", []), Analysis)
```

- **Config:** the pydantic model to validate against.

### `state_reducers`

Merge parallel subagent results into graph state without duplicates, by
upserting on a key.

```python
from typing import Annotated
from langshark_bites.state_reducers import envelope_reducer


class State(TypedDict):
    collected_outputs: Annotated[list[dict], envelope_reducer]
```

- **Config:** none — a state annotation change only.

### `observability`

OpenInference / Phoenix tracing with span decorators, without dragging the
full `arize-phoenix` SDK into the agent process.

```python
from langshark_bites.observability import init_phoenix, agent_span

init_phoenix(endpoint="http://localhost:6006", project_name="my-app")

@agent_span(parse_agent_name=True, tags={"as_of": "2026-07-10"})
async def run_worker(agent_name: str, as_of: str, ...):
    ...
```

- **Config:** `init_phoenix(endpoint=..., project_name=...)` once per process
  (under ASGI, call it via `asyncio.to_thread` / at startup — registration
  scans the SDK and can take seconds). It is idempotent and **soft-fails** if
  the collector is unreachable.
- **Prerequisite:** a Phoenix collector running as its own service (e.g. the
  official Docker container) — never inside the agent process.

### `a2a_completion_notifier`

Push completion notifications between split Agent Server deployments: an
emitter middleware on the subagent graph and a FastAPI receiver next to the
supervisor.

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
# Receiver (supervisor side): serve the app with uvicorn, next to the supervisor.
import uvicorn
from langshark_bites.a2a_completion_notifier.receiver import create_receiver_app
from langshark_bites.a2a_completion_notifier.settings import ReceiverSettings

app = create_receiver_app(settings=ReceiverSettings.from_env())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=ReceiverSettings.from_env().receiver_port)
```

- **Config:** `A2A_*` env vars — `A2A_SUPERVISOR_URL`, `A2A_SUPERVISOR_API_KEY`,
  `A2A_CALLBACK_TOKEN_SECRET`, receiver URL/host/port (defaults
  `http://localhost:8001` / `0.0.0.0` / `8001`), the subagent JWKS URL, and
  the signer's RSA private key + `kid`/`issuer`/`audience`.  An optional
  `A2A_REDIS_URL` enables cross-replica `jti` dedup.  Delivery failures
  surface as `a2a_forward_failed` / `a2a_forward_wake_failed` log events.
- **Prerequisite:** an RSA/JWKS key pair and a callback-token secret. Redis
  is optional — it enables cross-replica dedup (`RedisJtiStore`); the
  default is in-memory.
- **Install:** `uv add "langshark-bites[a2a-notifier]"`.

## Single verification gate

For each bite you integrate:

1. **Learn** from the bite's example — read `examples/<bite>.py` (in this repo
   or the docs site) to see the API in action. Every example runs **without
   external services** (Redis, Phoenix, live APIs, network).
2. **Wire** it into the project at the named integration point.
3. **Verify** with the project's own gate — the single test/lint command the
   consuming repo already uses.

An integration is done when that gate is green and the diff touches only the
intended call sites.

## Keeping this page honest

This page is instruction content for autonomous agents — it must stay in sync
with the bites' actual APIs. It is part of the docs build
(`uv run mkdocs build --strict` fails on broken links), so treat an edit to a
bite as an edit to this page too.
