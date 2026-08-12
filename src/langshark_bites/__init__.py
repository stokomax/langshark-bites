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

from .api_backoff import async_backoff
from .api_rate_limiter import RateLimitConfig, RateLimiter, rate_limited
from .json_output_parser import extract_structured_from_messages
from .observability import (
    agent_span,
    chain_span,
    get_tracer,
    init_phoenix,
    is_initialized,
    tool_span,
)
from .provider_failover import (
    CreditExhaustedCallback,
    ExhaustedProviderError,
    _exhausted,
    create_model_with_fallback,
    is_fallback_error,
)
from .state_reducers import envelope_reducer

__all__ = [
    "RateLimiter",
    "RateLimitConfig",
    "rate_limited",
    "CreditExhaustedCallback",
    "ExhaustedProviderError",
    "_exhausted",
    "create_model_with_fallback",
    "is_fallback_error",
    "async_backoff",
    "envelope_reducer",
    "extract_structured_from_messages",
    "init_phoenix",
    "get_tracer",
    "is_initialized",
    "agent_span",
    "chain_span",
    "tool_span",
]
