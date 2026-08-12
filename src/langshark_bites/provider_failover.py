"""Process-level circuit breaker for exhausted LLM provider credit/billing.

When an LLM provider returns a permanent credit/billing error (HTTP 400/401/403
with messages like "credit balance is too low"), this module marks the provider
as exhausted so every subsequent ``create_model()`` or ``create_model_with_fallback()``
call skips it immediately — zero wasted HTTP round-trips — and promotes the first
available fallback.

Standalone module with zero project-specific imports except ``structlog``.
Designed to be reusable across projects that use LangChain's ``create_agent``
with multiple LLM providers.

Usage
-----
    from langshark_bites.provider_failover import (
        _exhausted,
        CreditExhaustedCallback,
        create_model_with_fallback,
        ExhaustedProviderError,
    )

    # In your model factory, guard create_model():
    def create_model(model_name: str, max_tokens: int = 8192):
        prefix = model_name.split("-")[0]
        if _exhausted.exhausted(prefix):
            raise ExhaustedProviderError(prefix, model_name)
        ...

    # Attach the callback to provider model instances:
    model = ChatAnthropic(...)
    model.callbacks = [CreditExhaustedCallback("claude-sonnet-4-5")]

    # Replace manual ModelFallbackMiddleware with:
    model = create_model_with_fallback(
        "claude-sonnet-4-5",
        "deepseek-v4-flash,gpt-4o-mini",
        max_tokens=8192,
        model_builder=create_model,
    )
    # Pass the result directly as model= to create_agent().
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import structlog
from langchain_core.callbacks.base import BaseCallbackHandler

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Exhausted-provider registry (singleton)
# ---------------------------------------------------------------------------


class _ExhaustedProviders:
    """Process-level set of provider prefixes whose credit/billing is exhausted.

    Thread-safe by virtue of CPython's GIL for simple add/check operations.
    Once a provider is added, all subsequent ``create_model()`` calls for
    that provider raise ``ExhaustedProviderError`` immediately.

    Callers that cache model instances (e.g. agent caches) can register
    ``on_exhausted(callback)`` to clear their caches when a provider goes down.
    """

    def __init__(self):
        self._providers: set[str] = set()
        self._callbacks: list[Callable[[str], None]] = []

    def add(self, provider: str) -> None:
        """Mark *provider* as exhausted and fire all registered callbacks."""
        self._providers.add(provider)
        for cb in self._callbacks:
            try:
                cb(provider)
            except Exception:
                pass

    def exhausted(self, provider: str) -> bool:
        """Return True if *provider* is exhausted."""
        return provider in self._providers

    def on_exhausted(self, callback: Callable[[str], None]) -> None:
        """Register a callback invoked when any provider is marked exhausted.

        The callback receives the provider prefix (e.g. ``"claude"``).
        Use this to clear agent/model caches so the next build promotes
        a non-exhausted fallback to primary.
        """
        self._callbacks.append(callback)

    @property
    def all(self) -> frozenset[str]:
        """Snapshot of all exhausted providers (for logging)."""
        return frozenset(self._providers)


# Module-level singleton — shared across all callers in the same process.
_exhausted = _ExhaustedProviders()


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ExhaustedProviderError(Exception):
    """Raised when creating a model for an exhausted provider."""

    def __init__(self, provider: str, model_name: str):
        self.provider = provider
        self.model_name = model_name
        super().__init__(
            f"Provider '{provider}' is exhausted (credit/billing). "
            f"Use a fallback model instead of '{model_name}'."
        )


# ---------------------------------------------------------------------------
# Credit-exhaustion callback (attached to provider model instances)
# ---------------------------------------------------------------------------


class CreditExhaustedCallback(BaseCallbackHandler):
    """LangChain callback that detects credit/billing errors from LLM providers.

    Attach to model instances via ``model.callbacks = [callback]``.
    On a matching error, adds the provider to ``_exhausted`` so subsequent
    calls skip the provider entirely.

    Parameters
    ----------
    model_name : str
        Full model name (e.g. ``"claude-sonnet-4-5"``).  The provider prefix
        is extracted as ``model_name.split("-")[0]``.
    extra_patterns : list[str] | None
        Additional lowercase substrings to match in the error message.
        The built-in set covers Anthropic, OpenAI, and OpenRouter credit errors.
    """

    def __init__(
        self,
        model_name: str,
        extra_patterns: list[str] | None = None,
    ):
        self._model_name = model_name
        self._provider = model_name.split("-")[0]
        self._patterns = [
            "credit balance is too low",
            "insufficient balance",
            "insufficient_quota",
            "billing",
            "payment",
            "credits exhausted",
            "usage will exceed",
        ]
        if extra_patterns:
            self._patterns.extend(extra_patterns)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """Invoked by LangChain when a model call raises an exception."""
        msg = str(error).lower()
        if any(p in msg for p in self._patterns):
            _exhausted.add(self._provider)
            log.warning(
                "model_credit_balance_exhausted",
                model=self._model_name,
                provider=self._provider,
                exhausted_providers=sorted(_exhausted.all),
                error=str(error),
            )


# ---------------------------------------------------------------------------
# Exception classification (for .with_fallbacks() exception filtering)
# ---------------------------------------------------------------------------


def _pick_fallback_exc_types() -> tuple[type[Exception], ...]:
    """Collect exception classes that indicate permanent provider unavailability.

    Imports provider exception classes lazily so we don't fail at import time
    if a provider's package isn't installed.

    Returns:
        Tuple of exception classes to pass to ``.with_fallbacks()``.
    """
    exc_types: list[type[Exception]] = []
    try:
        from anthropic import AuthenticationError, BadRequestError, PermissionDeniedError
        exc_types.extend([BadRequestError, AuthenticationError, PermissionDeniedError])
    except ImportError:
        pass
    try:
        from openai import AuthenticationError, BadRequestError, PermissionDeniedError
        exc_types.extend([BadRequestError, AuthenticationError, PermissionDeniedError])
    except ImportError:
        pass
    return tuple(exc_types) if exc_types else (Exception,)


def is_fallback_error(exc: Exception) -> bool:
    """Return True if *exc* should trigger the model fallback chain.

    Matches permanent billing/credit/auth errors.  Rate-limit errors (429)
    are excluded — those are handled by retry middleware.
    """
    # Match by exception class name (works across provider packages)
    if type(exc).__name__ in {
        "BadRequestError",
        "AuthenticationError",
        "PermissionDeniedError",
    }:
        return True
    # Also catch credit/billing errors by message content for providers that
    # reuse generic exception classes.
    msg = str(exc).lower()
    return (
        "credit balance is too low" in msg
        or "insufficient balance" in msg
        or "insufficient_quota" in msg
        or "billing" in msg
        or "payment" in msg
        or "credits exhausted" in msg
        or "usage will exceed" in msg
    )


# ---------------------------------------------------------------------------
# Model factory with circuit breaker + fallback chain
# ---------------------------------------------------------------------------


def create_model_with_fallback(
    primary_name: str,
    fallbacks_csv: str,
    max_tokens: int = 8192,
    model_builder: Callable[[str, int], "BaseChatModel"] | None = None,
) -> "BaseChatModel":
    """Create a model with a baked-in fallback chain and circuit-breaker guard.

    The returned object is a LangChain ``RunnableWithFallbacks`` wrapping a
    primary model and ordered fallback models.  Pass it directly to
    ``create_agent(model=...)`` — no ``ModelFallbackMiddleware`` needed.

    If the primary provider is already marked as exhausted (from a prior call
    anywhere in the process), the first available non-exhausted fallback is
    promoted to primary immediately.  Only credit/billing/auth exceptions
    trigger fallback — rate-limit errors (429) are excluded and left to
    retry middleware.

    Args:
        primary_name: Primary model identifier (e.g. ``"claude-sonnet-4-5"``).
        fallbacks_csv: Comma-separated ordered fallback model names
            (e.g. ``"deepseek-v4-flash,gpt-4o-mini"``).  Empty string or
            whitespace-only → returns a plain model with no fallback chain.
        max_tokens: Maximum output tokens — applied to primary AND all fallbacks.
        model_builder: Function ``(model_name, max_tokens) → BaseChatModel``.
            Usually ``create_model`` from your model factory.  If None, the
            caller must set ``model_builder`` (required for circuit breaker).

    Returns:
        A ``BaseChatModel`` (or ``RunnableWithFallbacks`` wrapping one).
    """
    if model_builder is None:
        raise ValueError(
            "model_builder is required — pass your create_model function."
        )

    fallback_names = [n.strip() for n in fallbacks_csv.split(",") if n.strip()]

    # ---- Circuit breaker: skip exhausted primary ---------------------------
    primary_prefix = primary_name.split("-")[0]
    if _exhausted.exhausted(primary_prefix) and fallback_names:
        promoted = fallback_names[0]
        fallback_names = fallback_names[1:]
        log.warning(
            "model_provider_exhausted_skipping",
            primary=primary_name,
            promoted_fallback=promoted,
            remaining_fallbacks=fallback_names,
        )
        primary_name = promoted

    # ---- Also skip exhausted fallbacks -------------------------------------
    fallback_names = [
        n for n in fallback_names
        if not _exhausted.exhausted(n.split("-")[0])
    ]

    primary = model_builder(primary_name, max_tokens=max_tokens)

    if not fallback_names:
        log.info(
            "model_fallback_chain_configured",
            primary=primary_name,
            fallbacks=[],
        )
        return primary

    fallback_models = [
        model_builder(name, max_tokens=max_tokens) for name in fallback_names
    ]

    log.info(
        "model_fallback_chain_configured",
        primary=primary_name,
        fallbacks=fallback_names,
    )

    return primary.with_fallbacks(
        fallback_models,
        exceptions_to_handle=_pick_fallback_exc_types(),
    )
