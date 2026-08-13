"""OpenInference-style span decorators for agents, chains, and tools.

Wraps Phoenix OITracer's ``start_as_current_span`` with:

1. **Graceful no-op** when Phoenix is not initialized (safe in unit tests).
2. **OpenInference span kinds** (agent / chain / tool) for the Phoenix UI.
   OpenInference requires lowercase kind strings when passed as str.
3. **Filterable tags** via ``using_attributes(metadata=...)`` so spans carry
   keys you can filter on in Phoenix (as_of, task_id, version, model, ...).

Public API
----------
- ``agent_span(fn=None, *, default_override=None, parse_agent_name=False, tags=None)``
- ``chain_span(...)``  (same signature)
- ``tool_span(...)``   (same signature)

Span naming precedence (highest wins)
-------------------------------------
1. ``parse_agent_name=True`` -> the ``agent_name`` parameter's runtime value.
2. ``default_override`` -> a fixed literal name.
3. otherwise -> the wrapped function's name.

Usage::

    from langshark_bites.observability.phoenix import agent_span

    # Default: span is named after the function.
    @agent_span
    async def run_worker(agent_name: str):
        ...

    # Name each span after the agent that actually ran (worker loop).
    @agent_span(parse_agent_name=True, tags={"as_of": "2026-07-10"})
    async def run_worker(agent_name: str, as_of: str):
        ...

    # Fixed override + filterable tags.
    @agent_span(default_override="work", tags={"task_id": "x"})
    async def run_worker(agent_name: str):
        ...
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from langshark_bites.observability.phoenix.setup import phoenix_get_tracer

F = TypeVar("F", bound=Callable[..., Any])
Tags = dict[str, Any] | None

# OpenInference requires lowercase when kind is provided as a string.
_KIND_AGENT = "agent"
_KIND_CHAIN = "chain"
_KIND_TOOL = "tool"


def _bound_args(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Bind the actual call args to the function's parameter names.

    Returns a ``{param_name: value}`` mapping (with defaults applied) so
    downstream name resolution is a deterministic lookup by name, independent
    of whether the caller used positional or keyword arguments.  Returns
    ``{}`` if binding fails — observability must never break the app.
    """
    try:
        bound = inspect.signature(func).bind(*args, **kwargs)
        bound.apply_defaults()
        return bound.arguments
    except (TypeError, ValueError):
        return {}


def _resolve_span_name(
    default_override: str | None,
    fn_name: str,
    parse_agent_name: bool,
    bound: dict[str, Any],
) -> str:
    """Resolve the span name.

    Precedence (highest wins):
    1. ``parse_agent_name`` and an ``agent_name`` value -> that value.
    2. ``default_override`` -> that literal.
    3. otherwise -> ``fn_name``.
    """
    if parse_agent_name:
        agent = bound.get("agent_name")
        if isinstance(agent, str) and agent:
            return agent
        if agent is not None:
            return str(agent)
    if default_override:
        return default_override
    return fn_name


def agent_span(
    fn: F | None = None,
    /,
    *,
    default_override: str | None = None,
    parse_agent_name: bool = False,
    tags: Tags = None,
) -> Any:
    """Decorator: OpenInference AGENT span.  No-op if Phoenix uninitialized."""
    return _make_decorator(
        _KIND_AGENT, fn, default_override=default_override,
        parse_agent_name=parse_agent_name, tags=tags,
    )


def chain_span(
    fn: F | None = None,
    /,
    *,
    default_override: str | None = None,
    parse_agent_name: bool = False,
    tags: Tags = None,
) -> Any:
    """Decorator: OpenInference CHAIN span.  No-op if Phoenix uninitialized."""
    return _make_decorator(
        _KIND_CHAIN, fn, default_override=default_override,
        parse_agent_name=parse_agent_name, tags=tags,
    )


def tool_span(
    fn: F | None = None,
    /,
    *,
    default_override: str | None = None,
    parse_agent_name: bool = False,
    tags: Tags = None,
) -> Any:
    """Decorator: OpenInference TOOL span.  No-op if Phoenix uninitialized."""
    return _make_decorator(
        _KIND_TOOL, fn, default_override=default_override,
        parse_agent_name=parse_agent_name, tags=tags,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _make_decorator(
    span_kind: str,
    fn: F | None,
    *,
    default_override: str | None,
    parse_agent_name: bool,
    tags: Tags,
) -> Any:
    def decorator(func: F) -> F:
        is_async = inspect.iscoroutinefunction(func)

        if is_async:

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = phoenix_get_tracer()
                if tracer is None:
                    return await func(*args, **kwargs)
                bound = _bound_args(func, args, kwargs)
                span_name = _resolve_span_name(
                    default_override, func.__name__, parse_agent_name, bound
                )
                return await _invoke_async(
                    tracer, span_name, span_kind, tags, func, args, kwargs
                )

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = phoenix_get_tracer()
            if tracer is None:
                return func(*args, **kwargs)
            bound = _bound_args(func, args, kwargs)
            span_name = _resolve_span_name(
                default_override, func.__name__, parse_agent_name, bound
            )
            return _invoke_sync(tracer, span_name, span_kind, tags, func, args, kwargs)

        return sync_wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorator(fn)
    return decorator


def _span_cm(tracer: Any, span_name: str, span_kind: str, tags: Tags):
    """Build nested context managers: optional tags + agent/chain/tool span."""
    from contextlib import ExitStack

    stack = ExitStack()
    if tags:
        try:
            from phoenix.otel import using_attributes

            stack.enter_context(using_attributes(metadata=tags))
        except ImportError:
            pass

    stack.enter_context(
        tracer.start_as_current_span(
            span_name,
            openinference_span_kind=span_kind,
        )
    )
    return stack


def _invoke_sync(
    tracer: Any,
    span_name: str,
    span_kind: str,
    tags: Tags,
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    with _span_cm(tracer, span_name, span_kind, tags):
        return func(*args, **kwargs)


async def _invoke_async(
    tracer: Any,
    span_name: str,
    span_kind: str,
    tags: Tags,
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    with _span_cm(tracer, span_name, span_kind, tags):
        return await func(*args, **kwargs)
