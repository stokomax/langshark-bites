# Combining the bites

Each bite solves one problem on its own, but they are designed to work together. This page walks through one realistic scenario and shows where each bite fits.

## The scenario

You are building a market-analysis agent. It does the following:

1. Fetches news for a set of tickers from an external news API.
2. Sends each ticker's news to an LLM to produce a structured analysis.
3. Fans out to parallel subagents, one per ticker, and collects their results into the graph state.

This is a common multi-agent structure, and it hits every failure point the bites address.

## Step 1: Rate-limit the news API calls

The news API allows 100 requests per minute. You run three replicas of your agent server. A per-process limiter would let each replica make 100 requests per minute, so your effective rate is 300, and you get throttled.

`api_rate_limiter` keeps the budget in Redis, shared across all replicas, so the three processes share one 100-per-minute budget.

```python
from langshark_bites.api_rate_limiter import RateLimiter, rate_limited

limiter = RateLimiter.from_env()

@rate_limited(limiter, provider="newsapi")
async def fetch_news(ticker: str) -> str:
    ...
```

## Step 2: Back off and log when you still get throttled

Even with rate limiting, you can still get a 429. For example, the news API has a separate global limit you cannot predict. When that happens, you retry, and `api_backoff` makes the wait visible in the logs.

```python
from langshark_bites.api_backoff import async_backoff

async def fetch_news_with_retry(ticker: str) -> str:
    for attempt in range(1, 4):
        try:
            return await fetch_news(ticker)
        except ThrottledError:
            await async_backoff(2 ** attempt, context=f"newsapi retry {attempt}/3")
```

`api_rate_limiter` reduces how often you hit the limit. `api_backoff` handles the residual cases where you still get throttled. They are two halves of the same concern.

## Step 3: Build the LLM with a fallback chain

The analysis step calls an LLM provider. If that provider's credit runs out, every call wastes a round trip. `provider_failover` builds the model with a fallback chain, so a failed provider is skipped and a fallback model is used automatically.

```python
from langshark_bites.provider_failover import create_model_with_fallback

model = create_model_with_fallback(
    "claude-sonnet-4-5",
    "deepseek-v4-flash,gpt-4o-mini",
    max_tokens=8192,
    model_builder=create_model,
)
```

## Step 4: Parse structured output from a model that rejects `response_format`

The analysis model is DeepSeek in reasoning mode. It rejects `response_format` and returns free-text JSON in its message content, with reasoning noise before the actual output. `json_output_parser` extracts and validates it.

```python
from langshark_bites.json_output_parser import extract_structured_from_messages

content = state.get("structured_response")
if content is None:
    content = extract_structured_from_messages(state.get("messages", []), Analysis)
```

## Step 5: Merge parallel results without duplicates

You fan out to parallel subagents, one per ticker, using `Send`. Their results come back in random order. A plain `operator.add` would duplicate a row whenever a later node updates an existing entry (for example, a persist node flips `persisted=True`). `state_reducers` upserts by `task_id` so updates merge in place.

```python
from typing import Annotated
from langshark_bites.state_reducers import envelope_reducer

class State(TypedDict):
    collected_outputs: Annotated[list[dict], envelope_reducer]
```

## The full picture

Put together, the bites cover the whole lifecycle of the scenario:

| Stage | Bite | What it does |
|---|---|---|
| Before the news API call | `api_rate_limiter` | Shares one rate budget across all replicas |
| After a throttled news call | `api_backoff` | Waits and logs the retry |
| Building the LLM | `provider_failover` | Skips a down provider, uses a fallback model |
| Parsing the model output | `json_output_parser` | Extracts and validates free-text JSON |
| Merging parallel results | `state_reducers` | Upserts results by key, no duplicates |

You do not have to use all of them. Each bite works on its own. But when you have a multi-agent system that calls external APIs and LLM providers, these five cover the common failure points end to end.
