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

If you are building a multi-agent system with LangChain or LangGraph, your agents spend most of their time doing two things: calling external APIs (news feeds, databases, search) and calling LLM providers. Both of those calls can fail or get throttled in ways that are hard to see and hard to control. When you scale out to more replicas or more parallel subagents, those problems get more difficult to track.

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
- **`observability`** traces your agents so you can see which worker ran, what tools it called, and why a run was slow.

If you are new to LangGraph, the [quick note on terms](#a-quick-note-on-langgraph-terms) below explains the vocabulary. If you want to see how the bites work together in one realistic scenario, see [Combining the bites](combining.md).

## A scaling journey

Most multi-agent projects start small. You build a prototype on your laptop, one process, a handful of agents. It works. Then you deploy it, run more replicas, fan out more subagents, and the infrastructure problems start. The code that worked in the prototype starts getting throttled, or duplicating results, or wasting calls to a provider that ran out of credit.

The bites are built so you do not have to rewrite your code when that happens. You write against the same API whether you are running one process or a hundred. The difference is handled by the infrastructure, not by your code.

The clearest example is `api_rate_limiter`. On your laptop, with no Redis running, it uses an in-process token bucket. Your code is unchanged. When you deploy to many replicas and point it at Redis, it coordinates the budget across all of them. Same code, same calls, just a shared budget.

The other bites follow the same idea. `provider_failover`, `json_output_parser`, and `state_reducers` behave the same at small and large scale, because they do not depend on how many processes you run. `api_backoff` is the same in spirit: it reads the wait time (for example, from a `Retry-After` header), sleeps exactly that long, and logs a warning when the wait exceeds a configurable threshold — so it needs no shared state and is scale-agnostic by nature.

The practical result: you implement once, and the same code carries you from a single-process prototype to a multi-replica deployment. You add infrastructure as you grow, not code.

## Do you recognize these problems?

- Your agents call an external API, and once you run more than one replica, you start getting throttled. A per-process limit is not enough.
- For the LLM provider itself, transient failures and rate limits are best handled with LangChain's [`ModelRetryMiddleware`](https://docs.langchain.com/oss/python/langchain/middleware/built-in), which retries model calls with exponential backoff. `api_rate_limiter` does the same job, but for external API services that implement request rate limiting — it paces your requests against the provider's published limit so you get throttled less often.
- An API request gets throttled, you retry, but nobody can tell from the logs that a service is being throttled or for how long.
- An LLM provider's credit runs out in the middle of a run. Every call to that provider wastes a round trip, and you want to fall back to another model automatically.
- A model refuses to honor `response_format` and returns free-text JSON buried in reasoning noise. You want to use a powerful reasoning model like DeepSeek alongside models that do support structured output — without changing your code — so free-text results need to be parsed reliably.
- You fan out work to parallel subagents, and their results come back in random order. A plain list append duplicates rows when a later node updates an existing entry.
- You cannot see, from a deployed run, which sub-agent ran, what tools it called, or where the time went. You want distributed traces of your agents.

If any of these sound familiar, this package is for you.

## Quick start

Each bite solves one problem. Pick the one you are hitting, copy the happy path, and open the full docs for the details.

### Rate limit external API calls — `api_rate_limiter`

**Solves:** multiple replicas or subagents can exceed an external API's rate limit.

```python
from langshark_bites.api_rate_limiter import RateLimiter, rate_limited

limiter = RateLimiter.from_env()

@rate_limited(limiter, provider="newsapi")
async def fetch_news(ticker: str):
    ...
```

[Full docs: `api_rate_limiter`](api_rate_limiter.md)

### See throttled retries in the logs — `api_backoff`

**Solves:** when an external API throttles a request, the retry and its wait time are invisible in the logs, so operators cannot tell a service is being throttled or for how long.

```python
from langshark_bites.api_backoff import async_backoff

await async_backoff(wait, context="NewsAPI retry 1/3")
```

[Full docs: `api_backoff`](api_backoff.md)

### Fail over to a fallback LLM provider — `provider_failover`

**Solves:** an LLM provider's credit runs out mid-run and wastes calls.

```python
from langshark_bites.provider_failover import model_with_fallbacks

model = model_with_fallbacks(
    "claude-sonnet-4-5",
    "deepseek-v4-flash,gpt-4o-mini",
    max_tokens=8192,
    model_builder=create_model,
)
```

[Full docs: `provider_failover`](provider_failover.md)

### Parse free-text JSON from a model — `json_output_parser`

**Solves:** models that reject `response_format` return free-text JSON.

```python
from langshark_bites.json_output_parser import extract_structured_from_messages

content = extract_structured_from_messages(state.get("messages", []), MySchema)
```

[Full docs: `json_output_parser`](json_output_parser.md)

### Merge parallel results without duplicates — `state_reducers`

**Solves:** parallel workers duplicate rows when merging into graph state.

```python
from typing import Annotated
from langshark_bites.state_reducers import envelope_reducer

class State(TypedDict):
    collected_outputs: Annotated[list[dict], envelope_reducer]
```

[Full docs: `state_reducers`](state_reducers.md)

### Trace your agents — `observability`

**Solves:** you cannot tell which worker ran, what tools it called, or why a run was slow.

```python
from langshark_bites.observability import init_phoenix, agent_span

init_phoenix(endpoint="http://localhost:6006", project_name="my-app")

@agent_span(parse_agent_name=True)
async def run_worker(agent_name: str):
    ...
```

[Full docs: `observability`](observability.md)

## A quick note on LangGraph terms

If you are new to LangGraph, a few terms come up throughout these docs. Here is what they mean in plain language.

- **Graph state.** The shared data structure that flows through your agent graph. As nodes run, they read from and write to this state. It is how results accumulate across steps.
- **Node.** A single step in your graph. It takes the current state, does some work (often calling an API or an LLM), and returns an update to the state.
- **Reducer.** A function LangGraph calls to merge a node's update into the existing state. It decides how new values combine with old ones.
- **[`Send` fan-out](https://docs.langchain.com/oss/python/langgraph/reference/types#langgraph.types.Send).** A LangGraph pattern where one node dispatches many parallel workers in a single step. All workers run at once and their results merge back into the state.
- **Superstep.** A single step in which LangGraph runs several ready nodes at once, in parallel. When `Send` dispatches many tasks, they all run in the same superstep.
- **[`create_agent`](https://docs.langchain.com/oss/python/langchain/agents/create_agent).** A LangChain helper that builds an agent from a model and a set of tools.
- **Middleware.** Code that runs around each step an agent performs, and most importantly around the call to the LLM. Middleware wraps that call so it can observe or modify the request before it is sent and the response after it returns. In the agent-to-LLM flow, middleware sits between the agent and the model: it can inject a system prompt, capture the outgoing prompt, intercept tool calls, and record the model's output. Tracing and structured-output handling are typically implemented as middleware.
- **`response_format`.** A parameter that asks a model to return structured output (for example, JSON matching a schema) instead of free text.

## Installation

```bash
uv add langshark-bites
# or
pip install langshark-bites
```
