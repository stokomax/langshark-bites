# Tutorial: adding langshark-bites with an AI coding agent

This page is a worked example of the [integrating guide](integrating.md): an AI
coding agent adds langshark-bites to a real project, end to end. The example
project is [langstrata](https://github.com/stokomax/langstrata), a LangChain
blueprint for supervisor/worker deployments that scale from day one.

Read this page one of two ways:

- **You are a human** about to hand this job to an AI coding agent. This page is
your script: what to put in the task, what to leave to the agent, and what to
check when it is done.
- **You are an AI coding agent** asked to integrate langshark-bites into a
project. This page is a worked example of the method; follow the numbered steps
and adapt the integration points to the project you are actually in.

> [!NOTE] The two companion documents
>
> - [Integrating langshark-bites](integrating.md) — the reference: which
>   install, where each bite plugs in, how to verify. Read it first.
> - `templates/consumer/AGENTS.md` in the langshark-bites repo — the file you
>   copy into the consumer project so the agent has a single source of truth.

## The two projects

| | langshark-bites | langstrata (the consumer) |
|---|---|---|
| What it is | A library of bite-size add-ons for durable, scalable LangChain multi-agent systems | A blueprint for deploying multi-agent solutions with LangChain that scale from day one |
| Role in this tutorial | The library being integrated | The project being integrated into |
| Shape | 7 independent bites, each module + tests + docs + example | Supervisor + worker graphs running as independent Agent Servers |
| Key facts | Base package covers 6 bites; `a2a_completion_notifier` needs the `[a2a-notifier]` extra | `create_agent` workers, `create_deep_agent` + `AsyncSubAgent` supervisor, `StateGraph` + `Send` fan-out; PostgreSQL + Redis shared; Docker Compose |

langstrata is a good example because it is the shape of project the bites were
written for: several worker agents calling external APIs and LLM providers,
fanned out under a supervisor, deployed as separate Agent Servers. Its own
README points at langshark-bites in its "Next Steps".

## The method at a glance

An agentic integration is five steps. Nothing here requires a human to write
code — the human's job is scope and review.

1. **Set the agent up** — copy `templates/consumer/AGENTS.md` into the consumer
   repo and fill in the placeholders.
2. **The agent orients itself** — reads the context, maps the codebase, picks
   the integration points.
3. **Install** — `uv add langshark-bites`.
4. **Wire the bites** — one bite at a time: learn from its example, wire it at
   the named point, move on.
5. **Verify** — the consumer's own gate is green and the diff touches only the
   intended call sites.

## Step 0 — Set the agent up for success

Copy `templates/consumer/AGENTS.md` from the langshark-bites repo into the root
of the consumer project (or append it to an existing `AGENTS.md`). Fill in the
placeholders — for langstrata:

```markdown
## Project context
- langstrata: a blueprint for supervisor/worker multi-agent deployments.
- Supervisor (create_deep_agent + AsyncSubAgent) routes to researcher/coder/analyst
  workers (create_agent) that call external APIs and LLM providers.

## Bites in use
- [x] api_rate_limiter — web_search / fetch_url tools in src/langstrata/workers/researcher.py
- [x] api_backoff — retry loop around those tools
- [x] provider_failover — model construction in workers/base.py and supervisor/agent.py
- [ ] json_output_parser — (only if a worker returns free-text JSON)
- [x] state_reducers — Pattern B Send fan-out (planned)
- [x] observability — startup hook + worker/supervisor entry points
- [x] a2a_completion_notifier — emitter in create_worker_agent; receiver next to supervisor (compose sidecar)

## Single verification gate
- just lint    # uv run ruff check src/
- just format  # uv run ruff format --check src/
```

Then give the agent a one-line task. For example:

> Add langshark-bites to langstrata. Follow AGENTS.md. Wire the bites listed
> there at the integration points named. Keep langstrata's existing semantics.
> Verify with the gate in AGENTS.md.

The important part is what you **leave out**: real provider quotas, the fallback
model list, retry counts, and secrets are human-owned. The template's
"Developer decisions" section is where those live, and the agent should not
invent them.

## Step 1 — The agent orients itself

A good agent does not start editing. It reads, in this order:

1. The consumer's `AGENTS.md` — the single source of truth for this integration.
2. [The integrating reference](integrating.md) — which install, where each bite
   plugs in, the verification gate.
3. The bite docs and examples for the bites in scope — `docs/<bite>.md` and
   `examples/<bite>.py`. Every example runs without external services, so the
   agent can run it to see the API in action.
4. The consumer's code — for langstrata, the layout is small:

```
src/langstrata/
├── config.py            # Settings (AGENT_SERVER_* env prefix, mode asgi|http)
├── schemas.py           # WorkerTask, WorkerResult
├── supervisor/agent.py  # create_supervisor_agent() → create_deep_agent(..., subagents=[...])
└── workers/
    ├── base.py          # create_worker_agent() → create_agent(...)
    ├── researcher.py    # graph = create_worker_agent(...); web_search, fetch_url tools
    ├── coder.py
    └── analyst.py
```

What the agent should **ignore**:

- `main.py` at the langshark-bites repo root — a hello-world remnant, never an
  entrypoint.
- `skills/langshark-bites/SKILL.md` and `templates/bite/` — authoring internals
  for the library itself, not for consuming it.
- The consumer's deployment scaffolding (Dockerfiles, compose) — until a bite
  actually needs it (the a2a receiver does).

## Step 2 — Install

```bash
uv add langshark-bites
```

That covers six bites. The `a2a_completion_notifier` needs the extra (its
package imports the FastAPI receiver at module load, so even the emitter
requires it):

```bash
uv add "langshark-bites[a2a-notifier]"
```

## Step 3 — Wire the bites

Map the bites to langstrata's integration points:

| Bite | Where it plugs into langstrata | Learn from |
|---|---|---|
| `api_rate_limiter` | the external-API tools (`web_search`, `fetch_url` in `workers/researcher.py`) | `examples/rate_limiter.py` |
| `api_backoff` | the retry loop around those tools | `examples/backoff.py` |
| `provider_failover` | model construction — `workers/base.py` and `supervisor/agent.py` | `examples/provider_failover.py` |
| `json_output_parser` | worker free-text JSON → `WorkerResult` | `examples/json_output_parser.py` |
| `state_reducers` | Pattern B `Send` fan-out (planned) | `examples/state_reducers.py` |
| `observability` | startup hook + worker/supervisor entry points | `examples/observability.py` |
| `a2a_completion_notifier` | emitter in `create_worker_agent`; receiver next to the supervisor — compose sidecar (`create_receiver_app`) | `examples/a2a_completion_notifier.py` |

### api_rate_limiter — the researcher's tools

langstrata already runs Redis (shared with the worker server), so a shared
budget fits the architecture. Wrap the tool that calls the external API:

```python
# workers/researcher.py
from langshark_bites.api_rate_limiter import RateLimiter, rate_limited

limiter = RateLimiter.from_env()  # reads REDIS_URL


@tool
@rate_limited(limiter, provider="tavily")
async def web_search(query: str) -> str: ...
```

- The tool becomes async (LangChain tools support async; the limiter's
  `acquire` is `async`).
- langstrata's compose sets `REDIS_URI`; the limiter reads `REDIS_URL`. Point
  `REDIS_URL` at the same Redis (or construct `RateLimiter(redis_url=...)` from
  `Settings`).
- Define the `tavily` provider in a `rate_limits.yaml` (see
  `examples/rate_limits.example.yaml`) or via `RATE_LIMIT_TAVILY_RPM` env vars.
  There are no baked-in providers — you define the ones your project needs.
- Without Redis, the limiter falls back to an in-process bucket — langstrata
  keeps working locally and in tests.

### api_backoff — the retry loop

Even with a limiter, a 429 can still happen (a global cap you cannot predict).
Make the retry visible instead of using a bare `asyncio.sleep`:

```python
from langshark_bites.api_backoff import async_backoff


async def web_search_with_retry(query: str) -> str:
    for attempt in range(1, 4):
        try:
            return await web_search(query)
        except ThrottledError:
            await async_backoff(2**attempt, context=f"web_search retry {attempt}/3")
```

The `context` string lands in the log event, so operators can see which service
and which retry is being throttled.

### provider_failover — the model constructors

langstrata builds models from a single name (`Settings.model`, e.g.
`anthropic:claude-sonnet-5`) in two places: `create_worker_agent` and
`create_supervisor_agent`. Give each a fallback chain:

```python
# workers/base.py (and supervisor/agent.py)
from langshark_bites.provider_failover import model_with_fallbacks

def build_model(model_name: str, max_tokens: int = 8192):
    provider, _, name = model_name.partition(":")
    # ... construct the provider's chat model from (provider, name) ...

model = model_with_fallbacks(
    "anthropic:claude-sonnet-5",
    "openai:gpt-5,deepseek:deepseek-v4",  # human-owned: the fallback list
    max_tokens=8192,
    model_builder=build_model,
)
return create_agent(model=model, ...)
```

- The fallback list and order are a human decision — the agent should read it
  from config (or leave a placeholder), not invent it.
- 429s are excluded by design — they belong to retry middleware, not the
  fallback chain.

### json_output_parser — free-text JSON from a worker

If a worker model rejects `response_format` and returns free-text JSON (e.g.
DeepSeek thinking mode), parse it into langstrata's `WorkerResult`:

```python
from langshark_bites.json_output_parser import extract_structured_from_messages

result = extract_structured_from_messages(state.get("messages", []), WorkerResult)
```

### state_reducers — Pattern B Send fan-out

langstrata's Pattern B (planned) fans work out with
[`Send`](https://docs.langchain.com/oss/python/langgraph/reference/types#langgraph.types.Send)
and merges the results back into graph state. Merge without duplicates by
keying each worker's envelope on its name:

```python
from typing import Annotated
from langshark_bites.state_reducers import envelope_reducer


class State(TypedDict):
    results: Annotated[list[dict], envelope_reducer]  # envelope_id = worker name
```

### observability — trace supervisor and workers

Phoenix runs as its own service; the agent process only needs the slim OTEL
client that langshark-bites already pins. Register once at startup, decorate
the hot entry points:

```python
# at server startup (under ASGI: call via asyncio.to_thread — registration
# scans the SDK and can take seconds)
from langshark_bites.observability import agent_span, init_phoenix, tool_span

init_phoenix(endpoint="http://localhost:6006", project_name="langstrata")

@agent_span(parse_agent_name=True)
async def run_worker(agent_name: str, ...):
    ...

@tool
@tool_span(default_override="web_search")
def web_search(query: str) -> str:
    ...
```

- `init_phoenix` is idempotent and soft-fails if the collector is unreachable —
  tracing no-ops instead of breaking the app.
- Add the Phoenix collector as a compose service; never inside the agent
  process.

### a2a_completion_notifier — push completion to the supervisor

This is the bite langstrata's split-server topology was waiting for. Today the
supervisor polls `AsyncSubAgent` for completion; the notifier replaces polling
with push — an emitter on the worker side, a receiver next to the supervisor.

**Emitter** — thread middleware through `create_worker_agent`.  The signer is
optional: **`A2A_VERIFY_MODE=dev` (default) runs keyless** and the emitter sends
unsigned; pass a signer only to sign notifications for `verify`/`strict`:

```python
# workers/base.py
from langshark_bites.a2a_completion_notifier.middleware import (
    build_a2a_notifier_from_config,
)
from langshark_bites.a2a_completion_notifier.push_client import PushClient


def create_worker_agent(*, model, tools, system_prompt, **kwargs):
    notifier = build_a2a_notifier_from_config(
        config,
        signer=signer,  # optional: omit for keyless dev mode
        push_client=PushClient(),
    )
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=[notifier],
        **kwargs,
    )
```

**Receiver** — terminates `POST /a2a/notifications` next to the supervisor,
as a standalone FastAPI sidecar (a new service in the compose stack):

```python
from langshark_bites.a2a_completion_notifier.receiver import create_receiver_app
from langshark_bites.a2a_completion_notifier.settings import ReceiverSettings

app = create_receiver_app(settings=ReceiverSettings.from_env())
```

- **Modes** — the supervisor picks `A2A_VERIFY_MODE` (`dev` default / `verify` /
  `strict`).  In every mode the routing **callback token is mandatory**; the
  mode only governs sender verification: `dev` checks nothing else,
  `verify` verifies signed notifications (failing closed) while accepting
  unsigned ones, `strict` requires every notification to be signed.
- **Config** — `dev` needs only `A2A_CALLBACK_TOKEN_SECRET`; `verify`/`strict`
  additionally need the RSA/JWKS key plus issuer (via the `A2A_*` env vars).
  Redis is optional (`A2A_REDIS_URL`) — it enables cross-replica dedup;
  the default is in-memory.  (Delivery failures are surfaced as log events,
  see the reference page.)
- **Dev-server caveat** — the supervisor's `MailboxDrainMiddleware` reads
  `runtime.store`, which `langgraph dev` (the in-memory dev runtime) does not
  populate on middleware runtimes — it is a no-op there even when the graph is
  compiled with the server Store. The drain works on the platform / Agent
  Server (Postgres) runtime; for dev / self-hosted parity, read the pending
  notices over the LangGraph SDK `store` client (HTTP) instead of the runtime
  handle.
- This one changes the deployment, not just the code: the receiver is a
  compose sidecar process. The agent should flag that, not silently add a
  service to compose.

## Step 4 — Verify

The bites' own examples run without external services, so the agent can
sanity-check each API as it goes (run them from the langshark-bites checkout):

```bash
uv run python examples/rate_limiter.py
uv run python examples/backoff.py
uv run python examples/state_reducers.py
```

Then the consumer's own gate. For langstrata:

```bash
just lint    # uv run ruff check src/
just format  # uv run ruff format --check src/
```

An integration is done when the gate is green and the diff touches only the
intended call sites.

## Step 5 — Guardrails (what the agent must not do)

- **Do not add dependencies.** The base package covers everything except the
  `a2a-notifier` extra.
- **Do not rewrite langstrata's retry / fallback / error-handling semantics**
  unless the task explicitly says so.
- **Do not reach into private modules** — use only the public API re-exported
  from `langshark_bites` (and each bite's own `__init__.py`).
- **Do not guess human-owned values** — provider quotas, fallback lists, retry
  counts, secrets. Leave them as config placeholders.
- **Do not touch authoring internals** (`skills/langshark-bites/SKILL.md`,
  `templates/bite/`) or `main.py`.

## Step 6 — Human handoff

After the agent finishes, the human owns the remaining decisions (they are
listed in the consumer AGENTS.md template):

- [ ] Real provider quotas / limits
- [ ] Fallback model list and order
- [ ] Retry counts / backoff policy
- [ ] Secrets: Redis URL, Phoenix endpoint, a2a RSA key + callback-token
      secret, supervisor API key
- [ ] Final end-to-end run against real services (e.g. `just docker-up` +
      `just docker-run-demo`)

## What the finished diff looks like

For langstrata, a complete integration touches:

| File | Change |
|---|---|
| `pyproject.toml` | `uv add langshark-bites` (+ `[a2a-notifier]` if in scope) |
| `AGENTS.md` | the filled-in consumer template |
| `src/langstrata/workers/researcher.py` | rate-limited, backoff-retried, span-decorated tools |
| `src/langstrata/workers/base.py` | fallback model chain; a2a emitter middleware |
| `src/langstrata/supervisor/agent.py` | fallback model chain |
| `src/langstrata/schemas.py` | (optional) parse free-text JSON into `WorkerResult` |
| `src/langstrata/config.py` / `.env` | bite env vars (Redis URL, provider config, a2a secrets) |
| `docker-compose.yml` | (a2a only) receiver sidecar; (observability) Phoenix collector |

## Further reading

- [Integrating langshark-bites](integrating.md) — the reference this tutorial
  follows.
- [Combining the bites](combining.md) — the bites working together in one
  scenario.
- The consumer template: `templates/consumer/AGENTS.md` in the langshark-bites
  repo.
