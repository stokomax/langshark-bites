"""langshark-bites — bite-size add-ons and wrappers for building durable and
scalable LangChain multi-agent solutions.

This package provides small, reusable, project-agnostic building blocks:

- ``api_rate_limiter`` — distributed Redis-backed token-bucket rate limiter
  for external API calls.
- ``api_backoff`` — observable backoff sleep helper for retrying throttled
  external API calls.
- ``provider_failover`` — circuit breaker for exhausted LLM provider credit,
  plus model fallback-chain construction.
- ``json_output_parser`` — parse free-text JSON from models that reject
  ``response_format`` (e.g. DeepSeek thinking mode).
- ``state_reducers`` — LangGraph state reducers (e.g. upsert-by-key
  accumulation).
- ``observability`` — tracing backends (Phoenix/OpenInference today) with
  span decorators for agents, chains, and tools.
"""

from .api_backoff import async_backoff, retry_after_seconds
from .api_rate_limiter import RateLimitConfig, RateLimiter, rate_limited
from .json_output_parser import extract_structured_from_messages
from .observability import (
    agent_span,
    chain_span,
    init_phoenix,
    phoenix_get_tracer,
    phoenix_is_initialized,
    tool_span,
)
from .provider_failover import (
    ExhaustedProviderCallback,
    ExhaustedProviderError,
    create_model_with_fallback,
    is_fallback_error,
    is_provider_exhausted,
    mark_provider_exhausted,
    model_with_fallbacks,
    on_provider_exhausted,
)
from .state_reducers import envelope_reducer

__all__ = [
    "RateLimiter",
    "RateLimitConfig",
    "rate_limited",
    "ExhaustedProviderCallback",
    "ExhaustedProviderError",
    "create_model_with_fallback",
    "is_fallback_error",
    "is_provider_exhausted",
    "mark_provider_exhausted",
    "model_with_fallbacks",
    "on_provider_exhausted",
    "async_backoff",
    "retry_after_seconds",
    "envelope_reducer",
    "extract_structured_from_messages",
    "init_phoenix",
    "phoenix_get_tracer",
    "phoenix_is_initialized",
    "agent_span",
    "chain_span",
    "tool_span",
]
