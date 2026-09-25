# AGENTS.md — langshark-bites integration context

> **How to use this file.** Copy it into your project's root (or append its
> content to an existing `AGENTS.md`). It is the **single source of truth**
> for how this project uses langshark-bites. If you also keep `CLAUDE.md` or
> `.cursorrules`, point them at this file instead of duplicating its content.
> Review changes to this file with the same rigor as CI config — an
> autonomous agent will follow it.

## Project context

- {{ one line: what this project does }}
- {{ which agents / graphs use the bites }}

## Bites in use

Tick the bites this project uses and name where each plugs in:

- [ ] `api_rate_limiter` — {{ the function making the external call, e.g. `fetch_news` in `src/app.py` }}
- [ ] `api_backoff` — {{ the retry loop }}
- [ ] `provider_failover` — {{ the model constructor }}
- [ ] `json_output_parser` — {{ where the model's free-text JSON arrives }}
- [ ] `state_reducers` — {{ the graph-state key to upsert }}
- [ ] `observability` — {{ the startup hook + hot functions }}
- [ ] `a2a_completion_notifier` — {{ the subagent graph factory (emitter); the receiver: standalone FastAPI sidecar (`create_receiver_app`) next to the supervisor }}

## Install

```bash
uv add langshark-bites
# or, if a2a_completion_notifier is in use:
uv add "langshark-bites[a2a-notifier]"
```

## Docs

- Integration reference (read first): https://stokomax.github.io/langshark-bites/integrating/
- Per-bite docs + runnable examples: https://stokomax.github.io/langshark-bites/
- LangChain MCP reference (supervisor-side consumer): https://docs.langchain.com/oss/python/langchain/mcp

## Integration points

| Bite | File / symbol | Notes |
|---|---|---|
| `api_rate_limiter` | {{ ... }} | {{ e.g. provider name, quota }} |
| `api_backoff` | {{ ... }} | {{ retry count, context strings }} |
| `provider_failover` | {{ ... }} | {{ fallback list, model_builder }} |
| `json_output_parser` | {{ ... }} | {{ target pydantic model }} |
| `state_reducers` | {{ ... }} | {{ state key }} |
| `observability` | {{ ... }} | {{ endpoint, project_name }} |
| `a2a_completion_notifier` | {{ ... }} | {{ A2A_VERIFY_MODE (dev\|verify\|strict, default dev); A2A_* config; emitter signing key only required for verify/strict; A2A_RECEIVER_HOST/PORT + optional A2A_REDIS_URL for the FastAPI receiver sidecar. }} |

## Single verification gate

- {{ the project's one test/lint command, e.g. `uv run pytest` }}
- An integration is done when that gate is green and the diff touches only the
  intended call sites.

## Intentional divergences from common patterns

> Agents tend to repeat statistically common patterns and may "correct"
> deliberate choices. Record yours here so the agent does not "fix" them.

- {{ e.g. 429s stay with retry middleware; the fallback chain only handles credit/billing errors }}
- {{ e.g. the rate limiter uses the in-process fallback in dev, Redis only in prod }}
- {{ ... }}

## Developer decisions (human-owned — do not let the agent guess these)

- [ ] Which bites are in scope
- [ ] Real provider quotas / limits
- [ ] Fallback model list and order
- [ ] Retry counts / backoff policy
- [ ] Secrets: Redis URL, Phoenix endpoint, callback-token secret, supervisor API key; emitter RSA signing key (only for `verify`/`strict` a2a modes — `dev` runs without one)
- [ ] Final end-to-end run against real services
