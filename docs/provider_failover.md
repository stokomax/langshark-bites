# provider_failover

Circuit breaker for exhausted LLM provider credit, plus model fallback-chain construction.

## The problem

An LLM provider can return a permanent credit or billing error (for example, "credit balance is too low"). If you keep calling that provider, every call wastes a round trip. You want to skip it and use a fallback model instead, automatically.

## How this bite helps

It keeps a process-level registry of exhausted providers. When a provider is marked exhausted, every subsequent model build for that provider skips it immediately and promotes the first available fallback. `model_with_fallbacks` builds a LangChain [`RunnableWithFallbacks`](https://api.python.langchain.com/en/stable/runnables/langchain_core.runnables.fallbacks.RunnableWithFallbacks.html) with the circuit-breaker guard baked in.

The moving parts, and where each fits:

- **`is_provider_exhausted` / `mark_provider_exhausted` / `on_provider_exhausted`** — the circuit breaker. A provider key (e.g. `"claude"` from `"claude-sonnet-4-5"`) is marked exhausted for the lifetime of the process. `on_provider_exhausted` lets model/agent caches clear themselves when a provider goes down.
- **`ExhaustedProviderError`** — raised by your own `model_builder` when asked to build a model for an exhausted provider, so a fallback is chosen instead.
- **`ExhaustedProviderCallback`** — a LangChain `BaseCallbackHandler` you attach to a model instance (`model.callbacks = [ExhaustedProviderCallback("claude-sonnet-4-5")]`). Because it subclasses `BaseCallbackHandler`, LangChain's [callback system](https://python.langchain.com/docs/concepts/callbacks/) invokes its `on_llm_error` automatically whenever a call to that model raises an exception. The handler checks the error against known credit/billing patterns; on a match it marks the provider exhausted and logs a `model_credit_balance_exhausted` warning.
- **`is_fallback_error`** — the predicate for which exceptions should trigger LangChain's fallback chain (the `exceptions_to_handle` you would otherwise pass to [`with_fallbacks`](https://api.python.langchain.com/en/stable/core/runnables/langchain_core.runnables.base.Runnable.html#langchain_core.runnables.base.Runnable.with_fallbacks)). Rate-limit (429) errors are excluded — those belong to retry middleware.
- **`model_with_fallbacks`** — the entry point. Given a primary model name, a comma-separated fallback list, and your `model_builder`, it skips any exhausted provider and returns a [`RunnableWithFallbacks`](https://api.python.langchain.com/en/stable/runnables/langchain_core.runnables.fallbacks.RunnableWithFallbacks.html) (or a plain model when there are no fallbacks). Pass the result directly to [`create_agent(model=...)`](https://docs.langchain.com/oss/python/langchain/agents/create_agent).

**How it relates to LangChain's built-in middleware.** LangChain ships [`ModelRetryMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/model_retry/ModelRetryMiddleware) (retries the same model on transient errors such as 429s) and [`ModelFallbackMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/model_fallback/ModelFallbackMiddleware) (switches models when the primary fails). This module *augments* rather than replaces them: `ModelRetryMiddleware` handles transient 429 stalls by waiting and retrying the same model; this module handles **permanent** credit/billing failures (HTTP 400/401/402/403) by skipping the provider and switching models. Use `model_with_fallbacks` where you would otherwise wire [`ModelFallbackMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/model_fallback/ModelFallbackMiddleware), and keep [`ModelRetryMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/model_retry/ModelRetryMiddleware) for the 429 path.

## What topologies it supports

- [`create_agent`](https://docs.langchain.com/oss/python/langchain/agents/create_agent) with multiple LLM providers, where you want automatic fallback when one provider fails.
- Model factories that build models from a name, where you want a guard against exhausted providers.
- Agent or model caches that need to be cleared when a provider goes down, via the `on_provider_exhausted` callback.

## Configured vs exhausted providers

The module keeps a **process-level set of exhausted providers** — the provider keys (e.g. `"claude"`) that have been marked as credit/billing-exhausted. It does not track "configured" providers itself; your `model_with_fallbacks` call supplies the configured list (primary + fallbacks), and the module filters those against the exhausted set at build time.

Where the exhausted list shows up:

- **In the exception message.** `ExhaustedProviderError` is raised by your own `model_builder` when asked to build a model for an exhausted provider. Its message names the one provider that was requested and the model, e.g. `Provider 'claude' is exhausted (credit/billing). Use a fallback model instead of 'claude-sonnet-4-5'.` It names that single exhausted provider — it does not enumerate every exhausted provider. Use `is_provider_exhausted(name)` to test a specific provider, and `mark_provider_exhausted`/`on_provider_exhausted` to manage the set.
- **In the logs.** When a provider trips, the `model_credit_balance_exhausted` warning includes an `exhausted_providers` field with the full current set (sorted) — e.g. `exhausted_providers=['claude', 'deepseek']` — so operators can see all providers that are currently skipped.
- **In the fallback decision.** `model_with_fallbacks` filters the configured fallback list against the exhausted set at build time. It logs `model_provider_exhausted_skipping` (the skipped primary, the promoted fallback, and the remaining `fallbacks`) when the primary is exhausted, and `model_fallback_chain_configured` (the `primary` and the `fallbacks` that were actually built after dropping exhausted ones).

## What gets logged

The module emits structured log events (via `structlog`) so operators can see provider failures and fallback decisions.

| Event | Level | When | Fields |
|---|---|---|---|
| `model_credit_balance_exhausted` | warning | A provider returns a credit/billing error and is marked exhausted | `model`, `provider`, `exhausted_providers`, `error` |
| `model_provider_exhausted_skipping` | warning | An exhausted primary is skipped and a fallback is promoted | `primary`, `promoted_fallback`, `remaining_fallbacks` |
| `model_fallback_chain_configured` | info | A model is built with its fallback chain | `primary`, `fallbacks` |


## Example

See [examples/provider_failover.py](https://github.com/stokomax/langshark-bites/blob/main/examples/provider_failover.py) for a runnable example of this bite. Run it with:

```bash
uv run python examples/provider_failover.py
```

## API reference

::: langshark_bites.provider_failover
