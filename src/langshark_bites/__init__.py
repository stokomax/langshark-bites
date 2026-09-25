"""Bite-size add-ons and wrappers for durable, scalable LangChain multi-agent solutions.

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
- ``a2a_completion_notifier`` — A2A push completion notifications between
  split LangGraph deployments: a subagent-side emitter middleware and a
  supervisor-side receiver, packaged as an MCP server.
"""

try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _package_version

    __version__ = _package_version("langshark-bites")
except PackageNotFoundError:  # pragma: no cover - not installed (e.g. raw src checkout)
    __version__ = "0.0.0"

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
    "agent_span",
    "async_backoff",
    "chain_span",
    "create_model_with_fallback",
    "envelope_reducer",
    "ExhaustedProviderCallback",
    "ExhaustedProviderError",
    "extract_structured_from_messages",
    "init_phoenix",
    "is_fallback_error",
    "is_provider_exhausted",
    "mark_provider_exhausted",
    "model_with_fallbacks",
    "on_provider_exhausted",
    "phoenix_get_tracer",
    "phoenix_is_initialized",
    "rate_limited",
    "RateLimitConfig",
    "RateLimiter",
    "retry_after_seconds",
    "tool_span",
]
