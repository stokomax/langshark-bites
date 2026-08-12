# provider_failover

Circuit breaker for exhausted LLM provider credit, plus model fallback-chain construction.

## The problem

An LLM provider can return a permanent credit or billing error (for example, "credit balance is too low"). If you keep calling that provider, every call wastes a round trip. You want to skip it and use a fallback model instead, automatically.

## How this bite helps

It keeps a process-level registry of exhausted providers. When a provider is marked exhausted, every subsequent model build for that provider skips it immediately and promotes the first available fallback. `create_model_with_fallback` builds a LangChain `RunnableWithFallbacks` with the circuit-breaker guard baked in.

## What topologies it supports

- `create_agent` with multiple LLM providers, where you want automatic fallback when one provider fails.
- Model factories that build models from a name, where you want a guard against exhausted providers.
- Agent or model caches that need to be cleared when a provider goes down, via the `on_exhausted` callback.

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
